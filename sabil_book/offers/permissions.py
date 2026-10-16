from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sabil_book.offers.models import Offer
    from sabil_book.requests.models import Request
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


def is_direct_request_party(user: User, service_request: Request) -> bool:
    if not user.is_authenticated or service_request.selected_provider_id is None:
        return False
    return user.id in {
        service_request.customer_id,
        service_request.selected_provider.user_id,
    }


def can_view_direct_request_messages(user: User, service_request: Request) -> bool:
    from sabil_book.requests.models import Request  # noqa: PLC0415

    if service_request.visibility != Request.RequestVisibility.DIRECT:
        return False
    if user.is_staff:
        return True
    if user.id == service_request.customer_id:
        return True
    return service_request.status in {
        Request.RequestStatus.PUBLISHED,
        Request.RequestStatus.CLOSED,
    } and is_direct_request_party(user, service_request)


def can_message_direct_request(user: User, service_request: Request) -> bool:
    from sabil_book.requests.models import Request  # noqa: PLC0415

    return (
        service_request.visibility == Request.RequestVisibility.DIRECT
        and service_request.status == Request.RequestStatus.PUBLISHED
        and service_request.direct_status
        in {
            Request.DirectRequestStatus.PENDING,
            Request.DirectRequestStatus.ACCEPTED,
        }
        and is_direct_request_party(user, service_request)
    )
