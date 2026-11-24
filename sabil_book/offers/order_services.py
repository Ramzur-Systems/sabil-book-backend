"""Order state machine. All business transitions acquire the same row lock."""

from hashlib import sha256

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.reviews.models import Dispute
from sabil_book.transactions import on_commit_robust

from .antivirus import validate_result_file
from .models import Attachment
from .models import Order

S = Order.OrderStatus
ORDER_TRANSITIONS = {
    S.AWAITING_PAYMENT: {S.FUNDED},
    S.FUNDED: {S.DELIVERED},
    S.DELIVERED: {S.UNDER_REVIEW},
    S.UNDER_REVIEW: {S.COMPLETED, S.CORRECTION_REQUESTED, S.DISPUTED},
    S.CORRECTION_REQUESTED: {S.DELIVERED},
    S.COMPLETED: {S.PAID_OUT},
    S.DISPUTED: {S.COMPLETED, S.REFUNDED},
    S.PAID_OUT: set(),
    S.REFUNDED: set(),
}


def locked_order(order_id):
    # Lock only the Order row: joined nullable relations can break FOR UPDATE
    # on PostgreSQL. Related objects are fetched lazily within the transaction.
    return Order.objects.select_for_update().get(pk=order_id)


def require_customer(order, user):
    if order.offer.request.customer_id != user.pk:
        msg = "Only the customer may perform this action."
        raise PermissionDenied(msg)


def _complete(order):
    from sabil_book.payments.tasks import issue_order_payout  # noqa: PLC0415

    order.transition_to(S.COMPLETED)
    order.completed_at = timezone.now()
    order.review_deadline = None
    order.save(update_fields=["status", "completed_at", "review_deadline"])
    on_commit_robust(issue_order_payout.delay, order.pk)
    return order


@transaction.atomic
def upload_result(order_id, user, uploaded_file):
    from .tasks import scan_attachment  # noqa: PLC0415

    order = locked_order(order_id)
    if order.offer.provider.user_id != user.pk:
        msg = "Only the provider may upload results."
        raise PermissionDenied(msg)
    original_status = order.status
    order.transition_to(S.DELIVERED)
    order.status = original_status
    if order.attachments.filter(av_scan_status=Attachment.ScanStatus.PENDING).exists():
        msg = "A result is already awaiting antivirus scanning."
        raise InvalidTransitionError(msg)
    validate_result_file(uploaded_file)
    if not order.funded_at:
        msg = "Payment must be confirmed before delivery."
        raise InvalidTransitionError(msg)
    period = settings.ORDER_REVIEW_PERIOD_SECONDS
    if period <= 0:
        msg = "The review period must be positive."
        raise InvalidTransitionError(msg)
    digest = sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    attachment = Attachment(order=order, file_hash=digest.hexdigest())
    try:
        attachment.file.save(uploaded_file.name, uploaded_file, save=False)
        attachment.storage_key = attachment.file.name
        attachment.save()
        on_commit_robust(scan_attachment.delay, attachment.pk)
    except Exception:
        if attachment.file.name:
            attachment.file.delete(save=False)
        raise
    return order


def _review(order):
    if (
        order.attachments.exists()
        and not order.attachments.filter(
            av_scan_status=Attachment.ScanStatus.CLEAN,
        ).exists()
    ):
        msg = "No result has passed antivirus scanning."
        raise InvalidTransitionError(msg)
    if order.status == S.DELIVERED:
        order.transition_to(S.UNDER_REVIEW)
        order.save(update_fields=["status"])
    if order.status != S.UNDER_REVIEW:
        msg = "Only delivered results can be reviewed."
        raise InvalidTransitionError(msg)
    if order.review_deadline is None:
        msg = "The order has no review deadline."
        raise InvalidTransitionError(msg)
    if timezone.now() >= order.review_deadline:
        msg = "The review period has expired."
        raise InvalidTransitionError(msg)


@transaction.atomic
def accept_result(order_id, user):
    order = locked_order(order_id)
    require_customer(order, user)
    _review(order)
    return _complete(order)


@transaction.atomic
def request_correction(order_id, user, reason):
    order = locked_order(order_id)
    require_customer(order, user)
    _review(order)
    if not reason.strip():
        msg = "A correction reason is required."
        raise InvalidTransitionError(msg)
    order.transition_to(S.CORRECTION_REQUESTED)
    order.correction_reason = reason.strip()
    order.review_deadline = None
    order.save(update_fields=["status", "correction_reason", "review_deadline"])
    return order


@transaction.atomic
def open_dispute(order_id, user, reason):
    order = locked_order(order_id)
    require_customer(order, user)
    _review(order)
    if not reason.strip():
        msg = "A dispute reason is required."
        raise InvalidTransitionError(msg)
    order.transition_to(S.DISPUTED)
    order.review_deadline = None
    order.save(update_fields=["status", "review_deadline"])
    Dispute.objects.create(order=order, reason=reason.strip())
    return order


@transaction.atomic
def resolve_dispute(order_id, *, in_provider_favour, resolution):
    """Internal integration for trusted dispute handlers, never a public action.

    A refund remains disputed until the payment adapter confirms it.
    """
    from sabil_book.payments.tasks import refund_order  # noqa: PLC0415

    order = locked_order(order_id)
    if order.status != S.DISPUTED:
        msg = "Only an open dispute can be resolved."
        raise InvalidTransitionError(msg)
    dispute = Dispute.objects.get(order=order)
    if dispute.resolution or not resolution.strip():
        msg = "A new non-empty resolution is required."
        raise InvalidTransitionError(msg)
    dispute.resolution = resolution.strip()
    dispute.outcome = "provider" if in_provider_favour else "customer"
    dispute.save(update_fields=["resolution", "outcome"])
    if in_provider_favour:
        return _complete(order)
    on_commit_robust(refund_order.delay, order.pk)
    return order


@transaction.atomic
def process_review_timer(order_id):
    order = locked_order(order_id)
    if (
        order.attachments.exists()
        and not order.attachments.filter(
            av_scan_status=Attachment.ScanStatus.CLEAN,
        ).exists()
    ):
        return False
    if order.status not in {S.DELIVERED, S.UNDER_REVIEW}:
        return False
    if order.status == S.DELIVERED:
        order.transition_to(S.UNDER_REVIEW)
        order.save(update_fields=["status"])
    if order.review_deadline and order.review_deadline <= timezone.now():
        _complete(order)
        return True
    return False
