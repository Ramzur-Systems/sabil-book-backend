from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from sabil_book.offers.models import Message
from sabil_book.offers.models import Offer
from sabil_book.offers.models import Order
from sabil_book.offers.tests.factories import MessageFactory
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.tests.factories import ProviderProfileFactory

if TYPE_CHECKING:
    from sabil_book.users.models import User


class TestOffer:
    def test_defaults_to_pending_status(self, db):
        request = RequestFactory.create()
        provider = ProviderProfileFactory.create()
        offer = Offer.objects.create(
            request=request,
            provider=provider,
            price=Decimal("100.00"),
            delivery_days=7,
        )
        assert offer.status == Offer.OfferStatus.PENDING

    def test_comment_defaults_to_empty_string(self, db):
        offer = OfferFactory.create()
        assert offer.comment == ""

    @pytest.mark.parametrize("price", [Decimal("0.00"), Decimal("-0.01")])
    def test_price_must_be_positive(self, db, price):
        with pytest.raises(IntegrityError), transaction.atomic():
            OfferFactory.create(price=price)

    @pytest.mark.parametrize("delivery_days", [0, -1])
    def test_delivery_days_must_be_positive(self, db, delivery_days):
        with pytest.raises(IntegrityError), transaction.atomic():
            OfferFactory.create(delivery_days=delivery_days)

    def test_str_includes_provider_request_and_status(self, db):
        offer = OfferFactory.create(status=Offer.OfferStatus.ACCEPTED)
        assert str(offer) == f"{offer.provider} on {offer.request} (Accepted)"

    def test_deleted_when_request_is_deleted(self, db):
        offer = OfferFactory.create()
        offer.request.delete()
        assert not Offer.objects.filter(pk=offer.pk).exists()

    def test_deleted_when_provider_is_deleted(self, db):
        offer = OfferFactory.create()
        offer.provider.delete()
        assert not Offer.objects.filter(pk=offer.pk).exists()

    def test_request_can_receive_multiple_offers(self, db):
        offer_count = 3
        request = RequestFactory.create()
        OfferFactory.create_batch(offer_count, request=request)
        assert request.offers.count() == offer_count

    def test_request_cannot_have_multiple_accepted_offers(self, db):
        request = RequestFactory.create()
        OfferFactory.create(request=request, status=Offer.OfferStatus.ACCEPTED)

        with pytest.raises(IntegrityError), transaction.atomic():
            OfferFactory.create(request=request, status=Offer.OfferStatus.ACCEPTED)


class TestMessage:
    def test_str_truncates_long_body(self, db):
        offer = OfferFactory.create()
        message = MessageFactory.create(
            offer=offer,
            body="x" * 100,
        )
        assert str(message) == f"{message.sender}: {'x' * 30}"

    def test_deleted_when_offer_is_deleted(self, db):
        message = MessageFactory.create()
        message.offer.delete()
        assert not Message.objects.filter(pk=message.pk).exists()

    def test_deleted_when_sender_is_deleted(self, db, user: User):
        message = MessageFactory.create(sender=user)
        user.delete()
        assert not Message.objects.filter(pk=message.pk).exists()


class TestOrder:
    def test_defaults_to_funded_status(self, db):
        offer = OfferFactory.create()
        order = Order.objects.create(offer=offer)
        assert order.status == Order.OrderStatus.FUNDED

    def test_funded_at_defaults_to_none(self, db):
        offer = OfferFactory.create()
        order = Order.objects.create(offer=offer)
        assert order.funded_at is None

    def test_funded_at_can_be_set_explicitly(self, db):
        offer = OfferFactory.create()
        funded_at = timezone.now()
        order = Order.objects.create(offer=offer, funded_at=funded_at)
        order.refresh_from_db()
        assert order.funded_at == funded_at

    def test_str_is_cheap_and_shows_pk_and_status(self, db):
        order = OrderFactory.create(status=Order.OrderStatus.DISPUTE)
        assert str(order) == f"Order #{order.pk} (Dispute)"

    def test_deleted_when_offer_is_deleted(self, db):
        order = OrderFactory.create()
        order.offer.delete()
        assert not Order.objects.filter(pk=order.pk).exists()


@pytest.mark.django_db
def test_offer_factory_creates_valid_instance():
    offer = OfferFactory.create()
    assert offer.pk is not None
    assert offer.request_id is not None
    assert offer.provider_id is not None


@pytest.mark.django_db
def test_message_factory_creates_valid_instance():
    message = MessageFactory.create()
    assert message.pk is not None
    assert message.offer_id is not None
    assert message.sender_id is not None


@pytest.mark.django_db
def test_order_factory_creates_valid_instance():
    order = OrderFactory.create()
    assert order.pk is not None
    assert order.offer_id is not None
