"""Payments are hidden from customers and providers by default (PAYMENTS_ENABLED)."""

from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from sabil_book.notifications.models import NotificationDelivery
from sabil_book.offers.models import Order
from sabil_book.offers.order_services import accept_result
from sabil_book.offers.order_services import open_dispute
from sabil_book.offers.order_services import resolve_dispute
from sabil_book.offers.services import accept_offer
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.payments.models import Payment
from sabil_book.payments.tasks import recover_order_payments
from sabil_book.payments.tests.factories import PaymentFactory
from sabil_book.users.tests.factories import ProviderProfileFactory

S = Order.OrderStatus


@pytest.fixture(autouse=True)
def _payments_disabled(settings):
    settings.PAYMENTS_ENABLED = False


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _under_review(**kwargs):
    now = timezone.now()
    return OrderFactory(
        status=S.UNDER_REVIEW,
        funded_at=now,
        delivered_at=now,
        review_deadline=now + timezone.timedelta(days=1),
        **kwargs,
    )


def test_accepting_an_offer_skips_the_payment_step(db):
    offer = OfferFactory(request__status="published")
    accept_offer(offer.pk, offer.request.customer)
    order = Order.objects.get(offer=offer)
    assert order.status == S.FUNDED
    assert order.funded_at is not None
    assert not order.payments.exists()


@pytest.mark.parametrize("actor", ["customer", "provider"])
def test_pay_endpoint_is_hidden(db, actor):
    order = OrderFactory(status=S.AWAITING_PAYMENT, funded_at=None)
    user = (
        order.offer.request.customer
        if actor == "customer"
        else order.offer.provider.user
    )
    response = _client(user).post(f"/api/orders/{order.pk}/pay/")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not Payment.objects.exists()


@pytest.mark.parametrize("actor", ["customer", "provider"])
def test_order_responses_contain_no_payment_data(db, actor):
    order = OrderFactory(status=S.FUNDED, funded_at=timezone.now())
    user = (
        order.offer.request.customer
        if actor == "customer"
        else order.offer.provider.user
    )
    data = _client(user).get(f"/api/orders/{order.pk}/").data
    assert "funded_at" not in data
    assert not {"payment", "payments", "payout", "payouts"} & set(data)


def test_provider_profile_hides_payout_details(db):
    profile = ProviderProfileFactory(payout_provider="bank-account-123")
    client = _client(profile.user)
    assert "payout_provider" not in client.get("/api/providers/me/").data
    response = client.patch("/api/providers/me/", {"payout_provider": "changed"})
    assert response.status_code == status.HTTP_200_OK
    assert "payout_provider" not in response.data
    profile.refresh_from_db()
    assert profile.payout_provider == "bank-account-123"  # Not writable either.


def test_completion_queues_no_payout(db, django_capture_on_commit_callbacks):
    order = _under_review()
    with (
        patch("sabil_book.payments.tasks.issue_order_payout.delay") as payout,
        django_capture_on_commit_callbacks(execute=True),
    ):
        accept_result(order.pk, order.offer.request.customer)
    payout.assert_not_called()
    order.refresh_from_db()
    assert order.status == S.COMPLETED


def test_customer_favour_dispute_refunds_without_moving_money(
    db,
    django_capture_on_commit_callbacks,
):
    order = _under_review()
    open_dispute(order.pk, order.offer.request.customer, "Wrong result")
    with (
        patch("sabil_book.payments.tasks.refund_order.delay") as refund,
        django_capture_on_commit_callbacks(execute=True),
    ):
        resolved = resolve_dispute(
            order.pk,
            in_provider_favour=False,
            resolution="Staff decision",
        )
    refund.assert_not_called()
    assert resolved.status == S.REFUNDED
    assert resolved.refunded_at is not None  # Starts attachment retention.


def test_payment_recovery_does_nothing(db):
    PaymentFactory(status=Payment.PaymentStatus.PENDING)
    OrderFactory(status=S.COMPLETED)
    with (
        patch("sabil_book.payments.tasks.initiate_payment.delay") as pay,
        patch("sabil_book.payments.tasks.issue_order_payout.delay") as payout,
    ):
        recover_order_payments()
    pay.assert_not_called()
    payout.assert_not_called()


def test_payment_events_send_no_notifications(db):
    payment = PaymentFactory(status=Payment.PaymentStatus.PENDING)
    payment.status = Payment.PaymentStatus.SUCCEEDED
    payment.save()
    assert not NotificationDelivery.objects.filter(
        event__in=["payment_succeeded", "payout_sent"],
    ).exists()
