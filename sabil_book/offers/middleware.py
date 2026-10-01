from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework.authtoken.models import Token


@database_sync_to_async
def _get_user_from_token(token_key: str):
    try:
        return Token.objects.select_related("user").get(key=token_key).user
    except Token.DoesNotExist:
        return AnonymousUser()


class TokenAuthMiddleware:
    """Authenticates WebSocket connections using a DRF auth token.

    Browsers can't set an Authorization header during the WS handshake, so
    the frontend passes the token as a query parameter instead:
    ws://.../chat/?token=<key>. Must wrap the innermost app *inside*
    AuthMiddlewareStack, so session-based auth (already resolved into
    scope["user"] by then) is only overridden when a token is actually
    supplied and valid.
    """

    def __init__(self, inner) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["user"].is_anonymous:
            query_string = scope.get("query_string", b"").decode()
            token_key = parse_qs(query_string).get("token", [None])[0]
            if token_key:
                scope["user"] = await _get_user_from_token(token_key)
        return await self.inner(scope, receive, send)
