# ruff: noqa: PLR2004
"""Customer may dispute an order the provider never delivered on time."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.offers.models import Order
from sabil_book.offers.order_services import open_dispute
from sabil_book.offers.order_services import request_correction
from sabil_book.offers.order_services import resolve_dispute
from sabil_book.offers.order_services import upload_result
from sabil_book.offers.tasks import scan_attachment
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.payments.models import Payment
from sabil_book.payments.services import confirm_payment
from sabil_book.payments.tests.factories import PaymentFactory
from sabil_book.reviews.models import Dispute

S = Order.OrderStatus
# Delivery days go up to 30, so this keeps the review open past the due date.
REVIEW_OPEN_PAST_DUE_DATE = 60 * 24 * 3600


@pytest.fixture
def order(db):
    funded_at = timezone.now()
    order = OrderFactory(status=S.FUNDED, funded_at=funded_at)
    order.delivery_due_date = funded_at + timedelta(days=order.offer.delivery_days)
    order.save(update_fields=["delivery_due_date"])
    return order


@pytest.fixture
def client():
    return APIClient()


def customer(order):
    return order.offer.request.customer


def deliver(order):
    result = upload_result(
        order.pk,
        order.offer.provider.user,
        SimpleUploadedFile("result.txt", b"Result", content_type="text/plain"),
    )
    with patch("sabil_book.offers.antivirus.scan_stream", return_value="clean"):
        scan_attachment(result.attachments.latest("pk").pk)
    result.refresh_from_db()
    return result


def at(moment):
    return patch("django.utils.timezone.now", return_value=moment)


@pytest.mark.usefixtures("payments_enabled")
def test_funding_sets_due_date_from_delivery_days(db):
    order = OrderFactory(status=S.AWAITING_PAYMENT, funded_at=None)
    payment = PaymentFactory(order=order, status=Payment.PaymentStatus.PENDING)
    funded = confirm_payment(payment.pk)
    assert funded.delivery_due_date == funded.funded_at + timedelta(
        days=order.offer.delivery_days,
    )


def test_correction_extends_due_date_from_request_time(order, settings):
    settings.ORDER_REVIEW_PERIOD_SECONDS = REVIEW_OPEN_PAST_DUE_DATE
    deliver(order)
    moment = order.delivery_due_date + timedelta(days=2)
    with at(moment):
        corrected = request_correction(order.pk, customer(order), "Fix it")
    assert corrected.status == S.CORRECTION_REQUESTED
    assert corrected.delivery_due_date == moment + timedelta(
        days=order.offer.delivery_days,
    )


@pytest.mark.parametrize("status", [S.FUNDED, S.CORRECTION_REQUESTED])
def test_dispute_rejected_before_due_date(order, status):
    order.status = status
    order.save(update_fields=["status"])
    with (
        at(order.delivery_due_date - timedelta(microseconds=1)),
        pytest.raises(InvalidTransitionError),
    ):
        open_dispute(order.pk, customer(order), "Nothing delivered")
    order.refresh_from_db()
    assert order.status == status
    assert not Dispute.objects.filter(order=order).exists()


@pytest.mark.parametrize("status", [S.FUNDED, S.CORRECTION_REQUESTED])
def test_dispute_allowed_once_due_date_passes(order, status):
    order.status = status
    order.save(update_fields=["status"])
    with at(order.delivery_due_date):
        disputed = open_dispute(order.pk, customer(order), " Nothing delivered ")
    assert disputed.status == S.DISPUTED
    assert disputed.dispute.reason == "Nothing delivered"


def test_dispute_without_due_date_is_rejected(order):
    order.delivery_due_date = None
    order.save(update_fields=["delivery_due_date"])
    with pytest.raises(InvalidTransitionError):
        open_dispute(order.pk, customer(order), "Nothing delivered")
    order.refresh_from_db()
    assert order.status == S.FUNDED


def test_overdue_dispute_still_requires_reason(order):
    with (
        at(order.delivery_due_date + timedelta(days=1)),
        pytest.raises(InvalidTransitionError),
    ):
        open_dispute(order.pk, customer(order), "   ")
    order.refresh_from_db()
    assert order.status == S.FUNDED


def test_unpaid_order_cannot_be_disputed_even_if_overdue(order):
    order.status = S.AWAITING_PAYMENT
    order.save(update_fields=["status"])
    with (
        at(order.delivery_due_date + timedelta(days=1)),
        pytest.raises(InvalidTransitionError),
    ):
        open_dispute(order.pk, customer(order), "Nothing delivered")


def test_correction_resets_overdue_window(order, settings):
    """A dispute that was open before the correction closes again after it."""
    settings.ORDER_REVIEW_PERIOD_SECONDS = REVIEW_OPEN_PAST_DUE_DATE
    deliver(order)
    moment = order.delivery_due_date + timedelta(days=1)
    with at(moment):
        request_correction(order.pk, customer(order), "Fix it")
        with pytest.raises(InvalidTransitionError):
            open_dispute(order.pk, customer(order), "Still waiting")
    order.refresh_from_db()
    with at(order.delivery_due_date):
        assert open_dispute(order.pk, customer(order), "Still waiting").status == (
            S.DISPUTED
        )


@pytest.mark.parametrize(
    ("actor", "expected"),
    [("customer", 200), ("provider", 403)],
)
def test_overdue_dispute_api(order, client, actor, expected):
    users = {"customer": customer(order), "provider": order.offer.provider.user}
    client.force_authenticate(users[actor])
    with at(order.delivery_due_date + timedelta(days=1)):
        response = client.post(
            f"/api/orders/{order.pk}/dispute/",
            {"reason": "Nothing delivered"},
        )
    assert response.status_code == expected
    order.refresh_from_db()
    assert order.status == (S.DISPUTED if expected == 200 else S.FUNDED)


def test_early_dispute_api_returns_conflict(order, client):
    client.force_authenticate(customer(order))
    response = client.post(
        f"/api/orders/{order.pk}/dispute/",
        {"reason": "Nothing delivered"},
    )
    assert response.status_code == 409
    order.refresh_from_db()
    assert order.status == S.FUNDED


def test_order_api_exposes_due_date(order, client):
    client.force_authenticate(customer(order))
    response = client.get(f"/api/orders/{order.pk}/")
    assert response.status_code == 200
    assert response.data["delivery_due_date"] is not None


def test_overdue_dispute_can_be_refunded(order):
    with at(order.delivery_due_date + timedelta(days=1)):
        open_dispute(order.pk, customer(order), "Nothing delivered")
    resolved = resolve_dispute(
        order.pk,
        in_provider_favour=False,
        resolution="Provider never delivered",
    )
    assert resolved.status == S.REFUNDED
    assert resolved.refunded_at is not None
