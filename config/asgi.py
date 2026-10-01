"""
ASGI config for sabil_book project.

This module contains the ASGI application used to serve both plain HTTP
(handled by Django as usual) and WebSocket connections (handled by Django
Channels). It exposes a module-level variable named ``application``.

Usually you will only interact with this file if you want to add a new
protocol handler (e.g. `websocket`) or change the routing/middleware that
wraps it.

"""

import os
import sys
from pathlib import Path

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

# This allows easy placement of apps within the interior
# sabil_book directory.
BASE_DIR = Path(__file__).resolve(strict=True).parent.parent
sys.path.append(str(BASE_DIR / "sabil_book"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

# Populates Django's app registry before anything below is allowed to import
# models. Must run before `sabil_book.offers.routing` (and, transitively,
# `consumers.py`) is imported.
django_asgi_app = get_asgi_application()

from sabil_book.offers.middleware import TokenAuthMiddleware  # noqa: E402
from sabil_book.offers.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                TokenAuthMiddleware(URLRouter(websocket_urlpatterns)),
            ),
        ),
    },
)
