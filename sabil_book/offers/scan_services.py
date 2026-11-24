import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sabil_book.notifications.events import NotificationEvent
from sabil_book.notifications.services import NotificationService
from sabil_book.transactions import on_commit_robust

from .models import Attachment
from .models import Order
from .order_services import locked_order

logger = logging.getLogger(__name__)


@transaction.atomic
def finish_scan(attachment_id, result, error=""):
    # Same lock order as upload_result; duplicate worker deliveries are harmless.
    order_id = (
        Attachment.objects.filter(pk=attachment_id)
        .values_list("order_id", flat=True)
        .first()
    )
    if order_id is None:
        return
    order = locked_order(order_id)
    attachment = Attachment.objects.select_for_update().get(pk=attachment_id)
    if attachment.av_scan_status != Attachment.ScanStatus.PENDING:
        return
    if result == Attachment.ScanStatus.CLEAN and order.status not in {
        Order.OrderStatus.DELIVERED,
        Order.OrderStatus.UNDER_REVIEW,
        Order.OrderStatus.COMPLETED,
        Order.OrderStatus.DISPUTED,
        Order.OrderStatus.PAID_OUT,
        Order.OrderStatus.REFUNDED,
    }:
        if (
            order.status
            not in {Order.OrderStatus.FUNDED, Order.OrderStatus.CORRECTION_REQUESTED}
            or not order.funded_at
            or settings.ORDER_REVIEW_PERIOD_SECONDS <= 0
        ):
            result = Attachment.ScanStatus.ERROR
            error = "The order cannot publish this result."
        else:
            order.transition_to(Order.OrderStatus.DELIVERED)
            order.delivered_at = timezone.now()
            order.review_deadline = order.delivered_at + timedelta(
                seconds=settings.ORDER_REVIEW_PERIOD_SECONDS,
            )
            order.correction_reason = ""
            order.save(
                update_fields=[
                    "status",
                    "delivered_at",
                    "review_deadline",
                    "correction_reason",
                ],
            )
    attachment.av_scan_status = result
    attachment.av_scan_error = error
    attachment.av_scanned_at = timezone.now()
    update_fields = ["av_scan_status", "av_scan_error", "av_scanned_at"]
    if result != Attachment.ScanStatus.CLEAN and attachment.file:
        # Rejected objects are never served; drop them once the verdict commits.
        # storage_key keeps the original key for auditing.
        storage, key = attachment.file.storage, attachment.file.name
        on_commit_robust(storage.delete, key)
        attachment.file = ""
        update_fields.append("file")
    attachment.save(update_fields=update_fields)
    if result != Attachment.ScanStatus.CLEAN:
        _notify_rejected(order, attachment, result)


def _notify_rejected(order, attachment, result):
    # A savepoint keeps a notification failure from rolling back the verdict:
    # otherwise the upload would stay PENDING and block replacements.
    try:
        with transaction.atomic():
            NotificationService.notify(
                event=NotificationEvent.ATTACHMENT_REJECTED,
                recipient=order.offer.provider.user,
                context={
                    "order_id": order.pk,
                    "attachment_id": attachment.pk,
                    "scan_status": result,
                },
            )
    except Exception:
        logger.exception(
            "Could not notify the provider about rejected attachment %s.",
            attachment.pk,
        )
