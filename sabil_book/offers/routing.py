from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path(
        "ws/offers/<int:offer_id>/chat/",
        consumers.OfferChatConsumer.as_asgi(),
    ),
]
