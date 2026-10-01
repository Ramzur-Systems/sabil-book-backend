from django.db import transaction
from django.utils import timezone

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.requests.models import Request

from .models import Offer


@transaction.atomic
def accept_offer(offer_id: int, customer) -> Offer:
    service_request = Request.objects.select_for_update().get(
        offers__pk=offer_id,
        customer=customer,
    )
    offer = Offer.objects.get(
        pk=offer_id,
        request=service_request,
    )
    if service_request.status != Request.RequestStatus.PUBLISHED:
        msg = "Only an offer on a published request can be accepted."
        raise InvalidTransitionError(msg)

    if offer.status != Offer.OfferStatus.PENDING:
        msg = "Only a pending offer can be accepted."
        raise InvalidTransitionError(msg)

    Offer.objects.filter(
        request=service_request,
        status=Offer.OfferStatus.PENDING,
    ).exclude(pk=offer.pk).update(
        status=Offer.OfferStatus.REJECTED,
    )

    offer.status = Offer.OfferStatus.ACCEPTED
    offer.save(update_fields=["status"])

    service_request.status = Request.RequestStatus.CLOSED
    service_request.closed_at = timezone.now()
    service_request.save(
        update_fields=["status", "closed_at", "updated_at"],
    )

    return offer
