from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import Order
from .order_services import process_review_timer


@shared_task
def process_order_reviews():
    """Periodic DB-backed timer; safe to repeat after worker/broker downtime.

    No long-lived ETA messages: corrections clear the deadline and redelivery
    replaces it, so an old timer can never complete a new review round.
    """
    ids = Order.objects.filter(
        Q(status=Order.OrderStatus.DELIVERED)
        | Q(status=Order.OrderStatus.UNDER_REVIEW, review_deadline__lte=timezone.now()),
    ).values_list("pk", flat=True)
    completed = 0
    for order_id in ids.iterator():
        try:
            completed += process_review_timer(order_id)
        except Order.DoesNotExist:
            continue
    return completed


@shared_task(bind=True, max_retries=3, acks_late=True)
def scan_attachment(self, attachment_id):
    from .antivirus import ScanError  # noqa: PLC0415
    from .antivirus import scan_stream  # noqa: PLC0415
    from .antivirus import validate_document  # noqa: PLC0415
    from .models import Attachment  # noqa: PLC0415
    from .scan_services import finish_scan  # noqa: PLC0415

    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None or attachment.av_scan_status != Attachment.ScanStatus.PENDING:
        return
    try:
        with attachment.file.open("rb") as stream:
            result = scan_stream(stream, attachment.file_hash)
            if result == Attachment.ScanStatus.CLEAN:
                validate_document(stream, attachment.file.name)
    except ScanError as exc:
        finish_scan(attachment_id, Attachment.ScanStatus.ERROR, str(exc))
        return
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60) from exc
        finish_scan(
            attachment_id,
            Attachment.ScanStatus.ERROR,
            "Scanner or storage unavailable.",
        )
        return
    finish_scan(attachment_id, result)


@shared_task
def recover_attachment_scans():
    from .models import Attachment  # noqa: PLC0415

    # Pending rows are a durable outbox, including uploads made during outages.
    ids = Attachment.objects.filter(
        av_scan_status=Attachment.ScanStatus.PENDING,
    ).values_list("pk", flat=True)
    for attachment_id in ids.iterator():
        scan_attachment.delay(attachment_id)
