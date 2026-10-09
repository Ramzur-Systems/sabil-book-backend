from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from sabil_book.offers.models import Message
from sabil_book.offers.routing import websocket_urlpatterns
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory

RATE_LIMIT_FOR_TEST = 2

application = URLRouter(websocket_urlpatterns)


def _chat_path(offer_id: int) -> str:
    return f"/ws/offers/{offer_id}/chat/"


async def _connected_communicator(offer_id: int, user):
    communicator = WebsocketCommunicator(application, _chat_path(offer_id))
    communicator.scope["user"] = user
    connected, _ = await communicator.connect()
    assert connected
    return communicator


@pytest.mark.django_db(transaction=True)
def test_two_participants_see_each_others_messages_in_real_time():
    customer = UserFactory.create()
    provider_profile = ProviderProfileFactory.create()
    offer = OfferFactory.create(
        request=RequestFactory.create(customer=customer),
        provider=provider_profile,
    )

    async def run():
        customer_ws = await _connected_communicator(offer.id, customer)
        provider_ws = await _connected_communicator(offer.id, provider_profile.user)

        await customer_ws.send_json_to({"body": "Hello there"})

        customer_echo = await customer_ws.receive_json_from()
        provider_received = await provider_ws.receive_json_from()

        assert customer_echo["body"] == "Hello there"
        assert provider_received == customer_echo
        assert provider_received["sender"] == customer.id

        await customer_ws.disconnect()
        await provider_ws.disconnect()

    async_to_sync(run)()

    assert Message.objects.filter(offer=offer, body="Hello there").exists()


@pytest.mark.django_db(transaction=True)
def test_non_participant_is_rejected():
    offer = OfferFactory.create()
    stranger = UserFactory.create()

    async def run():
        communicator = WebsocketCommunicator(application, _chat_path(offer.id))
        communicator.scope["user"] = stranger
        connected, _ = await communicator.connect()
        assert not connected

    async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
def test_message_history_survives_a_reconnect():
    customer = UserFactory.create()
    offer = OfferFactory.create(request=RequestFactory.create(customer=customer))

    async def send_then_drop():
        communicator = await _connected_communicator(offer.id, customer)
        await communicator.send_json_to({"body": "first message"})
        await communicator.receive_json_from()
        await communicator.disconnect()

    async_to_sync(send_then_drop)()

    async def reconnect():
        communicator = await _connected_communicator(offer.id, customer)
        await communicator.disconnect()

    async_to_sync(reconnect)()

    assert list(
        Message.objects.filter(offer=offer).values_list("body", flat=True),
    ) == ["first message"]


@pytest.mark.django_db(transaction=True)
def test_rate_limit_blocks_flooding_without_dropping_earlier_messages(
    settings,
    monkeypatch,
):
    settings.CHAT_MESSAGE_RATE_LIMIT = RATE_LIMIT_FOR_TEST
    settings.CHAT_MESSAGE_RATE_LIMIT_WINDOW = 10
    customer = UserFactory.create()
    offer = OfferFactory.create(request=RequestFactory.create(customer=customer))
    message_count = 0

    def is_rate_limited(_user_id: int, _offer_id: int) -> bool:
        nonlocal message_count
        message_count += 1
        return message_count > RATE_LIMIT_FOR_TEST

    monkeypatch.setattr("sabil_book.offers.consumers.is_rate_limited", is_rate_limited)

    async def run():
        communicator = await _connected_communicator(offer.id, customer)

        for i in range(RATE_LIMIT_FOR_TEST):
            await communicator.send_json_to({"body": f"msg {i}"})
            await communicator.receive_json_from()

        await communicator.send_json_to({"body": "one too many"})
        response = await communicator.receive_json_from()
        assert response == {"error": "rate_limited"}

        await communicator.disconnect()

    async_to_sync(run)()

    assert Message.objects.filter(offer=offer).count() == RATE_LIMIT_FOR_TEST
