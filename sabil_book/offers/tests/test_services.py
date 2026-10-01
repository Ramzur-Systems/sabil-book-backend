import pytest

from sabil_book.offers.models import Offer
from sabil_book.offers.services import InvalidTransitionError
from sabil_book.offers.services import accept_offer
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.requests.models import Request
from sabil_book.requests.tests.factories import RequestFactory


@pytest.mark.django_db
def test_accept_offer_accepts_selection_rejects_competitors_and_closes_request():
    service_request = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    selected = OfferFactory(request=service_request)
    competing = OfferFactory(request=service_request)
    unrelated = OfferFactory()

    result = accept_offer(selected.pk, service_request.customer)

    selected.refresh_from_db()
    competing.refresh_from_db()
    unrelated.refresh_from_db()
    service_request.refresh_from_db()
    assert result == selected
    assert selected.status == Offer.OfferStatus.ACCEPTED
    assert competing.status == Offer.OfferStatus.REJECTED
    assert unrelated.status == Offer.OfferStatus.PENDING
    assert service_request.status == Request.RequestStatus.CLOSED
    assert service_request.closed_at is not None


@pytest.mark.django_db
def test_accept_offer_rejects_invalid_request_state():
    service_request = RequestFactory(status=Request.RequestStatus.CLOSED)
    offer = OfferFactory(request=service_request)

    with pytest.raises(InvalidTransitionError):
        accept_offer(offer.pk, service_request.customer)

    offer.refresh_from_db()
    assert offer.status == Offer.OfferStatus.PENDING
