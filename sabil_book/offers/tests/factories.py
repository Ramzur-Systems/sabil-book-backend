from __future__ import annotations

import factory
from factory import Faker
from factory import fuzzy
from factory.django import DjangoModelFactory

from sabil_book.offers.models import Message
from sabil_book.offers.models import Offer
from sabil_book.offers.models import Order
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory


class OfferFactory(DjangoModelFactory[Offer]):
    request = factory.SubFactory(RequestFactory)
    provider = factory.SubFactory(ProviderProfileFactory)
    price = fuzzy.FuzzyDecimal(10, 500, precision=2)
    delivery_days = fuzzy.FuzzyInteger(1, 30)
    status = Offer.OfferStatus.PENDING

    class Meta:
        model = Offer


class MessageFactory(DjangoModelFactory[Message]):
    offer = factory.SubFactory(OfferFactory)
    sender = factory.SubFactory(UserFactory)
    body = Faker("sentence")

    class Meta:
        model = Message


class OrderFactory(DjangoModelFactory[Order]):
    offer = factory.SubFactory(OfferFactory)
    status = fuzzy.FuzzyChoice(Order.OrderStatus.values)

    class Meta:
        model = Order
