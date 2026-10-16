from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sabil_book.offers.models import Offer
    from sabil_book.users.models import User


def is_offer_participant(user: User, offer: Offer) -> bool:
    """True if `user` may see/join this offer's chat.

    That's the request's customer or the offer's provider, and nobody else.
    Shared between OfferChatConsumer (WebSocket) and MessageListView (REST
    history) so the two access checks can't drift apart.
    """
    if not user.is_authenticated:
        return False
    return user.id in {offer.request.customer_id, offer.provider.user_id}
