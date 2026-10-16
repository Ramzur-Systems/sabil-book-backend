from __future__ import annotations

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from sabil_book.requests.models import Request

from .models import Message
from .models import Offer
from .permissions import can_message_direct_request
from .permissions import is_offer_participant
from .rate_limit import is_rate_limited
from .serializers import MessageSerializer

MAX_MESSAGE_LENGTH = Message._meta.get_field("body").max_length  # noqa: SLF001

# Close codes in the 4000-4999 range are reserved for application use.
WS_CLOSE_FORBIDDEN = 4403


class OfferChatConsumer(AsyncJsonWebsocketConsumer):
    """One chat room per Offer, shared by its request's customer and its provider."""

    async def connect(self) -> None:
        self.offer_id = self.scope["url_route"]["kwargs"]["offer_id"]
        self.group_name = f"offer_chat_{self.offer_id}"

        if not await self._user_is_participant():
            await self.close(code=WS_CLOSE_FORBIDDEN)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code) -> None:
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs) -> None:
        raw_body = content.get("body")
        body = raw_body.strip() if isinstance(raw_body, str) else ""
        if not body or len(body) > MAX_MESSAGE_LENGTH:
            await self.send_json({"error": "invalid_message"})
            return

        user = self.scope["user"]
        if await database_sync_to_async(is_rate_limited)(
            user.id,
            f"offer:{self.offer_id}",
        ):
            await self.send_json({"error": "rate_limited"})
            return

        message_data = await self._save_message(user, body)
        await self.channel_layer.group_send(
            self.group_name,
            {"type": "chat.message", "message": message_data},
        )

    async def chat_message(self, event) -> None:
        await self.send_json(event["message"])

    @database_sync_to_async
    def _user_is_participant(self) -> bool:
        user = self.scope["user"]
        if not user.is_authenticated:
            return False
        try:
            offer = Offer.objects.select_related("request", "provider").get(
                pk=self.offer_id,
            )
        except Offer.DoesNotExist:
            return False
        return is_offer_participant(user, offer)

    @database_sync_to_async
    def _save_message(self, user, body: str) -> dict:
        message = Message.objects.create(offer_id=self.offer_id, sender=user, body=body)
        return MessageSerializer(message).data


class DirectRequestChatConsumer(AsyncJsonWebsocketConsumer):
    """A private request-level chat for a client and their selected provider."""

    async def connect(self) -> None:
        self.request_id = self.scope["url_route"]["kwargs"]["request_id"]
        # The room is per (request, provider): if a declined request is re-sent
        # to another provider, sockets still open on the old thread must not
        # receive the new conversation.
        self.provider_id = await self._messageable_provider_id()
        if self.provider_id is None:
            await self.close(code=WS_CLOSE_FORBIDDEN)
            return

        self.group_name = f"direct_request_chat_{self.request_id}_{self.provider_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code) -> None:
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs) -> None:
        raw_body = content.get("body")
        body = raw_body.strip() if isinstance(raw_body, str) else ""
        if not body or len(body) > MAX_MESSAGE_LENGTH:
            await self.send_json({"error": "invalid_message"})
            return

        if await self._messageable_provider_id() != self.provider_id:
            await self.send_json({"error": "forbidden"})
            return

        user = self.scope["user"]
        if await database_sync_to_async(is_rate_limited)(
            user.id,
            f"request:{self.request_id}",
        ):
            await self.send_json({"error": "rate_limited"})
            return

        message_data = await self._save_message(user, body)
        await self.channel_layer.group_send(
            self.group_name,
            {"type": "chat.message", "message": message_data},
        )

    async def chat_message(self, event) -> None:
        await self.send_json(event["message"])

    @database_sync_to_async
    def _messageable_provider_id(self) -> int | None:
        """The current thread's provider, or None if the user may not message."""
        user = self.scope["user"]
        if not user.is_authenticated:
            return None
        try:
            service_request = Request.objects.select_related(
                "selected_provider",
            ).get(pk=self.request_id)
        except Request.DoesNotExist:
            return None
        if not can_message_direct_request(user, service_request):
            return None
        return service_request.selected_provider_id

    @database_sync_to_async
    def _save_message(self, user, body: str) -> dict:
        message = Message.objects.create(
            request_id=self.request_id,
            provider_id=self.provider_id,
            sender=user,
            body=body,
        )
        return MessageSerializer(message).data
