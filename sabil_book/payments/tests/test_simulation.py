from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from sabil_book.offers.models import Order
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.payments.models import Payment
from sabil_book.payments.services import initiate_order_payment
from sabil_book.users.tests.factories import UserFactory


@pytest.fixture
def unpaid_order(db, settings):
    settings.PAYMENT_SIMULATION_ENABLED = True
    return OrderFactory(status=Order.OrderStatus.AWAITING_PAYMENT, funded_at=None)


@pytest.mark.parametrize(
    ("country", "currency"),
    [("KZ", "KZT"), ("QA", "QAR"), ("US", "USD"), ("", "USD")],
)
def test_simulated_payment_immediately_funds_order_without_worker(
    unpaid_order, country, currency,
):
    customer = unpaid_order.offer.request.customer
    customer.country = country
    customer.save()
    client = APIClient()
    client.force_authenticate(customer)
    with patch("sabil_book.payments.tasks.initiate_payment.delay") as enqueue:
        response = client.post(f"/api/orders/{unpaid_order.pk}/pay/")
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["provider"] == "simulated"
    assert response.data["status"] == Payment.PaymentStatus.SUCCEEDED
    assert response.data["currency"] == currency
    assert response.data["amount"] == str(unpaid_order.offer.price)
    enqueue.assert_not_called()
    unpaid_order.refresh_from_db()
    assert unpaid_order.status == Order.OrderStatus.FUNDED
    assert unpaid_order.funded_at is not None
    assert (
        client.post(f"/api/orders/{unpaid_order.pk}/pay/").status_code
        == status.HTTP_409_CONFLICT
    )
    assert unpaid_order.payments.count() == 1


def test_simulation_uses_configured_country_currency(unpaid_order, settings):
    customer = unpaid_order.offer.request.customer
    customer.country = "kz"
    settings.PAYMENT_PROVIDERS_BY_COUNTRY = {
        "KZ": {"provider": "real", "currency": "USD"},
    }
    payment = initiate_order_payment(unpaid_order.pk, customer)
    assert payment.currency == "USD"
    assert payment.provider == "simulated"


@pytest.mark.parametrize("actor", ["provider", "stranger", "anonymous"])
def test_simulation_keeps_payment_access_controls(unpaid_order, actor):
    client = APIClient()
    users = {
        "provider": unpaid_order.offer.provider.user,
        "stranger": UserFactory(),
        "anonymous": None,
    }
    client.force_authenticate(users[actor])
    response = client.post(f"/api/orders/{unpaid_order.pk}/pay/")
    expected = {"provider": 403, "stranger": 404, "anonymous": 401}
    assert response.status_code == expected[actor]
    assert not Payment.objects.filter(order=unpaid_order).exists()


def test_simulation_can_be_disabled(unpaid_order, settings):
    settings.PAYMENT_SIMULATION_ENABLED = False
    settings.PAYMENT_PROVIDERS_BY_COUNTRY = {}
    client = APIClient()
    client.force_authenticate(unpaid_order.offer.request.customer)
    response = client.post(f"/api/orders/{unpaid_order.pk}/pay/")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    unpaid_order.refresh_from_db()
    assert unpaid_order.status == Order.OrderStatus.AWAITING_PAYMENT
    assert not unpaid_order.payments.exists()
