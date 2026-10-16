from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from sabil_book.offers.models import Message
from sabil_book.offers.routing import websocket_urlpatterns
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.requests.models import Request
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory

RATE_LIMIT_FOR_TEST = 2

application = URLRouter(websocket_urlpatterns)


def _chat_path(offer_id: int) -> str:
    return f"/ws/offers/{offer_id}/chat/"


def _direct_chat_path(request_id: int) -> str:
    return f"/ws/requests/{request_id}/chat/"


async def _connected_communicator(offer_id: int, user):
    communicator = WebsocketCommunicator(application, _chat_path(offer_id))
    communicator.scope["user"] = user
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _connected_direct_communicator(request_id: int, user):
    communicator = WebsocketCommunicator(application, _direct_chat_path(request_id))
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
def test_direct_request_participants_can_chat_before_an_offer_exists():
    provider = ProviderProfileFactory.create()
    service_request = RequestFactory.create(
        direct=True,
        selected_provider=provider,
        status=Request.RequestStatus.PUBLISHED,
    )

    async def run():
        customer_ws = await _connected_direct_communicator(
            service_request.pk,
            service_request.customer,
        )
        provider_ws = await _connected_direct_communicator(
            service_request.pk,
            provider.user,
        )

        await provider_ws.send_json_to({"body": "Could you clarify the scope?"})
        provider_echo = await provider_ws.receive_json_from()
        customer_received = await customer_ws.receive_json_from()

        assert provider_echo["request"] == service_request.pk
        assert provider_echo["offer"] is None
        assert customer_received == provider_echo

        await provider_ws.disconnect()
        await customer_ws.disconnect()

    async_to_sync(run)()

    assert Message.objects.filter(
        request=service_request,
        offer__isnull=True,
        body="Could you clarify the scope?",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_direct_request_chat_rejects_other_providers_and_declined_requests():
    service_request = RequestFactory.create(
        direct=True,
        status=Request.RequestStatus.PUBLISHED,
    )
    stranger = ProviderProfileFactory.create()

    async def stranger_cannot_connect():
        communicator = WebsocketCommunicator(
            application,
            _direct_chat_path(service_request.pk),
        )
        communicator.scope["user"] = stranger.user
        connected, _ = await communicator.connect()
        assert not connected

    async_to_sync(stranger_cannot_connect)()

    service_request.direct_status = Request.DirectRequestStatus.DECLINED
    service_request.save(update_fields=["direct_status"])

    async def selected_provider_cannot_connect_after_decline():
        communicator = WebsocketCommunicator(
            application,
            _direct_chat_path(service_request.pk),
        )
        communicator.scope["user"] = service_request.selected_provider.user
        connected, _ = await communicator.connect()
        assert not connected

    async_to_sync(selected_provider_cannot_connect_after_decline)()


@pytest.mark.django_db(transaction=True)
def test_reassigned_direct_request_does_not_leak_into_previous_thread():
    previous_provider = ProviderProfileFactory.create()
    service_request = RequestFactory.create(
        direct=True,
        selected_provider=previous_provider,
        status=Request.RequestStatus.PUBLISHED,
    )
    new_provider = ProviderProfileFactory.create()

    async def run():
        stale_customer_ws = await _connected_direct_communicator(
            service_request.pk,
            service_request.customer,
        )
        previous_provider_ws = await _connected_direct_communicator(
            service_request.pk,
            previous_provider.user,
        )

        await database_sync_to_async(
            Request.objects.filter(pk=service_request.pk).update,
        )(
            selected_provider=new_provider,
            direct_status=Request.DirectRequestStatus.PENDING,
        )

        await stale_customer_ws.send_json_to({"body": "Sent on the old thread"})
        assert await stale_customer_ws.receive_json_from() == {"error": "forbidden"}

        customer_ws = await _connected_direct_communicator(
            service_request.pk,
            service_request.customer,
        )
        new_provider_ws = await _connected_direct_communicator(
            service_request.pk,
            new_provider.user,
        )
        await customer_ws.send_json_to({"body": "Hello new provider"})
        received = await new_provider_ws.receive_json_from()
        assert received["body"] == "Hello new provider"
        assert await previous_provider_ws.receive_nothing()

        for communicator in [
            stale_customer_ws,
            previous_provider_ws,
            customer_ws,
            new_provider_ws,
        ]:
            await communicator.disconnect()

    async_to_sync(run)()

    assert list(
        Message.objects.filter(request=service_request).values_list(
            "provider_id",
            "body",
        ),
    ) == [(new_provider.pk, "Hello new provider")]


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
