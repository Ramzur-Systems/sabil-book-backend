from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models.signals import post_save
from django.db.models.signals import pre_save
from django.dispatch import receiver

from sabil_book.offers.models import Offer
from sabil_book.offers.models import Order
from sabil_book.payments.models import Payment
from sabil_book.payments.models import Payout
from sabil_book.reviews.models import Dispute

from .events import NotificationEvent
from .services import NotificationService

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_notifications_enabled: ContextVar[bool] = ContextVar(
    "notifications_enabled",
    default=True,
)


@contextmanager
def notifications_disabled():
    """Suppress signal-driven notifications, e.g. while bulk-seeding data."""
    token = _notifications_enabled.set(False)
    try:
        yield
    finally:
        _notifications_enabled.reset(token)


def _safe_receiver(func: Callable) -> Callable:
    """Never let a notification failure break the business save that triggered it.

    The work runs in a savepoint so a failed insert cannot poison the caller's
    transaction.
    """

    @wraps(func)
    def wrapper(sender, instance, **kwargs) -> None:
        if not _notifications_enabled.get():
            return
        try:
            with transaction.atomic():
                func(sender, instance, **kwargs)
        except Exception:
            logger.exception(
                "Could not create notifications for %s #%s.",
                sender.__name__,
                instance.pk,
            )

    return wrapper


# Previous values are captured in pre_save, so only changes made through
# ``Model.save()`` notify. Bulk ``QuerySet.update()`` calls bypass signals; the
# only one today (``accept_offer`` rejecting competing offers) has no
# notification, but a future bulk update that should notify must call
# ``NotificationService`` itself.
def _remember_previous_field(
    sender,
    instance,
    field_name: str,
    update_fields=None,
) -> None:
    attr = f"notification_previous_{field_name}"
    if update_fields is not None and field_name not in update_fields:
        # The field is not being written, so it cannot have changed: skip the
        # extra SELECT and don't reuse a stale value from an earlier save.
        setattr(instance, attr, getattr(instance, field_name))
        return
    previous_value = None
    if instance.pk:
        previous_value = (
            sender.objects.filter(pk=instance.pk)
            .values_list(field_name, flat=True)
            .first()
        )
    setattr(instance, attr, previous_value)


def _field_changed(instance, field_name: str, current_value: str) -> bool:
    return (
        getattr(instance, f"notification_previous_{field_name}", None) != current_value
    )


def _order_context(order: Order) -> dict[str, str | int]:
    return {
        "order_id": order.pk,
        "offer_id": order.offer_id,
        "request_id": order.offer.request_id,
        "request_title": order.offer.request.title,
    }


@receiver(pre_save, sender=Offer, dispatch_uid="notifications.offer.previous_status")
@receiver(pre_save, sender=Order, dispatch_uid="notifications.order.previous_status")
@receiver(
    pre_save,
    sender=Payment,
    dispatch_uid="notifications.payment.previous_status",
)
@receiver(pre_save, sender=Payout, dispatch_uid="notifications.payout.previous_status")
def remember_previous_status(sender, instance, update_fields=None, **kwargs) -> None:
    del kwargs
    _remember_previous_field(sender, instance, "status", update_fields)


@receiver(
    pre_save,
    sender=Dispute,
    dispatch_uid="notifications.dispute.previous_resolution",
)
def remember_previous_resolution(
    sender,
    instance,
    update_fields=None,
    **kwargs,
) -> None:
    del kwargs
    _remember_previous_field(sender, instance, "resolution", update_fields)


@receiver(post_save, sender=Offer, dispatch_uid="notifications.offer.events")
@_safe_receiver
def notify_offer_events(sender, instance: Offer, *, created: bool, **kwargs) -> None:
    del sender, kwargs
    context = {
        "offer_id": instance.pk,
        "request_id": instance.request_id,
        "request_title": instance.request.title,
        "price": str(instance.price),
        "provider_name": instance.provider.user.name or instance.provider.user.email,
    }
    if created:
        NotificationService.notify(
            event=NotificationEvent.NEW_OFFER,
            recipient=instance.request.customer,
            context=context,
        )
    if instance.status == Offer.OfferStatus.ACCEPTED and _field_changed(
        instance,
        "status",
        Offer.OfferStatus.ACCEPTED,
    ):
        NotificationService.notify(
            event=NotificationEvent.OFFER_SELECTED,
            recipient=instance.provider.user,
            context=context,
        )


@receiver(post_save, sender=Payment, dispatch_uid="notifications.payment.events")
@_safe_receiver
def notify_payment_events(sender, instance: Payment, **kwargs) -> None:
    del sender, kwargs
    if instance.status != Payment.PaymentStatus.SUCCEEDED or not _field_changed(
        instance,
        "status",
        Payment.PaymentStatus.SUCCEEDED,
    ):
        return
    order = instance.order
    NotificationService.notify_many(
        event=NotificationEvent.PAYMENT_SUCCEEDED,
        recipients=[order.offer.request.customer, order.offer.provider.user],
        context={
            **_order_context(order),
            "payment_id": instance.pk,
            "amount": str(instance.amount),
            "currency": instance.currency,
        },
    )


# Order status -> (event, who is told). The recipient is resolved lazily so the
# offer, request and user rows are only loaded for statuses that notify.
_ORDER_EVENTS: dict[str, tuple[NotificationEvent, Callable[[Order], object]]] = {
    Order.OrderStatus.SENT: (
        NotificationEvent.RESULT_DELIVERED,
        lambda order: order.offer.request.customer,
    ),
    Order.OrderStatus.CONFIRMED: (
        NotificationEvent.ACCEPTANCE_CONFIRMED,
        lambda order: order.offer.provider.user,
    ),
    Order.OrderStatus.REVISION_REQUESTED: (
        NotificationEvent.REVISION_REQUESTED,
        lambda order: order.offer.provider.user,
    ),
}


@receiver(post_save, sender=Order, dispatch_uid="notifications.order.events")
@_safe_receiver
def notify_order_events(sender, instance: Order, *, created: bool, **kwargs) -> None:
    del sender, kwargs
    # A freshly created order is not a status transition.
    if created or instance.status not in _ORDER_EVENTS:
        return
    if not _field_changed(instance, "status", instance.status):
        return
    event, get_recipient = _ORDER_EVENTS[instance.status]
    NotificationService.notify(
        event=event,
        recipient=get_recipient(instance),
        context=_order_context(instance),
    )


@receiver(post_save, sender=Dispute, dispatch_uid="notifications.dispute.events")
@_safe_receiver
def notify_dispute_events(
    sender,
    instance: Dispute,
    *,
    created: bool,
    **kwargs,
) -> None:
    del sender, kwargs
    order = instance.order
    recipients = [order.offer.request.customer, order.offer.provider.user]
    context = {
        **_order_context(order),
        "dispute_id": instance.pk,
        "reason": instance.reason,
        "resolution": instance.resolution,
    }
    if created:
        NotificationService.notify_many(
            event=NotificationEvent.DISPUTE_OPENED,
            recipients=recipients,
            context=context,
        )
    previous_resolution = getattr(
        instance,
        "notification_previous_resolution",
        None,
    )
    if instance.resolution and not previous_resolution:
        NotificationService.notify_many(
            event=NotificationEvent.DISPUTE_RESOLVED,
            recipients=recipients,
            context=context,
        )


@receiver(post_save, sender=Payout, dispatch_uid="notifications.payout.events")
@_safe_receiver
def notify_payout_events(sender, instance: Payout, **kwargs) -> None:
    del sender, kwargs
    if instance.status != Payout.PayoutStatus.PAID or not _field_changed(
        instance,
        "status",
        Payout.PayoutStatus.PAID,
    ):
        return
    NotificationService.notify(
        event=NotificationEvent.PAYOUT_SENT,
        recipient=instance.order.offer.provider.user,
        context={
            **_order_context(instance.order),
            "payout_id": instance.pk,
            "amount": str(instance.amount),
            "currency": instance.currency,
        },
    )
