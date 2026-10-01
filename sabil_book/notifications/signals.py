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


def _remember_previous_field(sender, instance, field_name: str) -> None:
    previous_value = None
    if instance.pk:
        previous_value = (
            sender.objects.filter(pk=instance.pk)
            .values_list(field_name, flat=True)
            .first()
        )
    setattr(instance, f"notification_previous_{field_name}", previous_value)


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
def remember_previous_status(sender, instance, **kwargs) -> None:
    del kwargs
    _remember_previous_field(sender, instance, "status")


@receiver(
    pre_save,
    sender=Dispute,
    dispatch_uid="notifications.dispute.previous_resolution",
)
def remember_previous_resolution(sender, instance, **kwargs) -> None:
    del kwargs
    _remember_previous_field(sender, instance, "resolution")


@receiver(post_save, sender=Offer, dispatch_uid="notifications.offer.events")
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


@receiver(post_save, sender=Order, dispatch_uid="notifications.order.events")
def notify_order_events(sender, instance: Order, **kwargs) -> None:
    del sender, kwargs
    if not _field_changed(instance, "status", instance.status):
        return
    event_and_recipient_by_status: dict[
        str,
        tuple[NotificationEvent, object],
    ] = {
        Order.OrderStatus.SENT: (
            NotificationEvent.RESULT_DELIVERED,
            instance.offer.request.customer,
        ),
        Order.OrderStatus.CONFIRMED: (
            NotificationEvent.ACCEPTANCE_CONFIRMED,
            instance.offer.provider.user,
        ),
        Order.OrderStatus.REVISION_REQUESTED: (
            NotificationEvent.REVISION_REQUESTED,
            instance.offer.provider.user,
        ),
    }
    event_and_recipient = event_and_recipient_by_status.get(instance.status)
    if event_and_recipient is None:
        return
    event, recipient = event_and_recipient
    NotificationService.notify(
        event=event,
        recipient=recipient,
        context=_order_context(instance),
    )


@receiver(post_save, sender=Dispute, dispatch_uid="notifications.dispute.events")
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
