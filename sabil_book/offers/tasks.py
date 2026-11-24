from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from sabil_book.transactions import on_commit_robust

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


SCAN_UNAVAILABLE = "Scanner or storage unavailable."


def _scan_expired(attachment):
    age = timezone.now() - attachment.created_at
    return age >= timedelta(seconds=settings.ATTACHMENT_SCAN_MAX_PENDING_SECONDS)


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
    if not attachment.file:
        finish_scan(attachment_id, Attachment.ScanStatus.ERROR, "No stored file.")
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
        # Infrastructure outage: stay PENDING so recovery retries later, and
        # only fail closed once the upload has waited longer than the max age.
        if _scan_expired(attachment):
            finish_scan(attachment_id, Attachment.ScanStatus.ERROR, SCAN_UNAVAILABLE)
        return
    finish_scan(attachment_id, result)


@shared_task
def recover_attachment_scans():
    from .models import Attachment  # noqa: PLC0415
    from .scan_services import finish_scan  # noqa: PLC0415

    # Pending rows are a durable outbox, including uploads made during outages.
    now = timezone.now()
    pending = Attachment.objects.filter(
        av_scan_status=Attachment.ScanStatus.PENDING,
    )
    max_age = timedelta(seconds=settings.ATTACHMENT_SCAN_MAX_PENDING_SECONDS)
    expired = pending.filter(created_at__lte=now - max_age)
    for attachment_id in expired.values_list("pk", flat=True):
        finish_scan(attachment_id, Attachment.ScanStatus.ERROR, SCAN_UNAVAILABLE)
    # Skip fresh uploads and rows recovery queued recently: their task chain
    # (initial run plus retries) may still be in flight.
    grace = timedelta(seconds=settings.ATTACHMENT_SCAN_RECOVERY_GRACE_SECONDS)
    idle_before = now - grace
    stale = pending.filter(created_at__lte=idle_before).filter(
        Q(av_scan_queued_at__isnull=True) | Q(av_scan_queued_at__lte=idle_before),
    )
    ids = list(stale.values_list("pk", flat=True))
    Attachment.objects.filter(pk__in=ids).update(av_scan_queued_at=now)
    for attachment_id in ids:
        scan_attachment.delay(attachment_id)


@shared_task
def purge_expired_attachments():
    """Delete results (rows and objects) a retention period after the order ends.

    Customers keep access after completion; only attachments are removed, never
    the order or its payment records. Objects are deleted after the row commits;
    if that delete fails, sweep_orphan_attachments removes the object later.
    """
    from .models import Attachment  # noqa: PLC0415

    cutoff = timezone.now() - timedelta(days=settings.ATTACHMENT_RETENTION_DAYS)
    expired = Attachment.objects.filter(
        Q(
            order__status__in=[Order.OrderStatus.COMPLETED, Order.OrderStatus.PAID_OUT],
            order__completed_at__lte=cutoff,
        )
        | Q(order__status=Order.OrderStatus.REFUNDED, order__refunded_at__lte=cutoff),
    )
    purged = 0
    for attachment in expired.iterator():
        with transaction.atomic():
            if attachment.file:
                storage, key = attachment.file.storage, attachment.file.name
                on_commit_robust(storage.delete, key)
            attachment.delete()
        purged += 1
    return purged


def _stored_keys(storage, prefix):
    dirs, files = storage.listdir(prefix)
    for name in files:
        yield f"{prefix}/{name}"
    for directory in dirs:
        yield from _stored_keys(storage, f"{prefix}/{directory}")


@shared_task
def sweep_orphan_attachments():
    """Delete stored results no Attachment row references.

    Uploads write to storage inside the request transaction; a rollback after
    that write leaves an object behind that no on_commit hook can clean up.
    """
    from .models import Attachment  # noqa: PLC0415
    from .storage import attachment_storage  # noqa: PLC0415

    storage = attachment_storage()
    grace = timedelta(seconds=settings.ATTACHMENT_ORPHAN_GRACE_SECONDS)
    cutoff = timezone.now() - grace
    deleted = 0
    try:
        keys = list(_stored_keys(storage, "results"))
    except FileNotFoundError:
        return 0
    for key in keys:
        if Attachment.objects.filter(file=key).exists():
            continue
        if storage.get_modified_time(key) > cutoff:
            continue  # May belong to an upload whose transaction is still open.
        storage.delete(key)
        deleted += 1
    return deleted
