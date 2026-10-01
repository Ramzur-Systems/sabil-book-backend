from decimal import Decimal
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import IntegrityError
from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from sabil_book.offers.models import Offer
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.offers.views import OfferViewSet
from sabil_book.requests.models import Request
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.models import ProviderProfile
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.mark.django_db
def test_verified_provider_can_create_offer_for_published_request(api_client):
    price = Decimal("150.00")
    delivery_days = 5
    service_request = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(provider.user)

    response = api_client.post(
        reverse("api:offer-list"),
        {
            "request": service_request.pk,
            "price": str(price),
            "delivery_days": delivery_days,
            "comment": "I can complete this request within five days.",
        },
    )

    assert response.status_code == HTTPStatus.CREATED
    offer = Offer.objects.get()
    assert offer.request == service_request
    assert offer.provider == provider
    assert offer.price == price
    assert offer.delivery_days == delivery_days
    assert offer.comment == "I can complete this request within five days."
    assert offer.status == Offer.OfferStatus.PENDING


@pytest.mark.django_db
def test_duplicate_active_offer_returns_bad_request(api_client):
    service_request = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    OfferFactory(request=service_request, provider=provider)
    api_client.force_authenticate(provider.user)

    response = api_client.post(
        reverse("api:offer-list"),
        {
            "request": service_request.pk,
            "price": "150.00",
            "delivery_days": 5,
        },
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "request" in response.data
    assert Offer.objects.filter(request=service_request, provider=provider).count() == 1


@pytest.mark.django_db
def test_duplicate_offer_race_returns_validation_error():
    service_request = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    database_cause = Exception()
    database_cause.diag = SimpleNamespace(
        constraint_name="unique_active_offer_per_provider_per_request",
    )
    integrity_error = IntegrityError()
    integrity_error.__cause__ = database_cause
    serializer = Mock(validated_data={"request": service_request})
    serializer.save.side_effect = integrity_error
    view = OfferViewSet()
    view.request = SimpleNamespace(user=provider.user)

    with pytest.raises(ValidationError) as exc_info:
        view.perform_create(serializer)

    assert "request" in exc_info.value.detail


@pytest.mark.django_db
def test_user_without_provider_profile_cannot_create_offer(api_client):
    api_client.force_authenticate(UserFactory())

    response = api_client.post(
        reverse("api:offer-list"),
        {
            "request": RequestFactory(status=Request.RequestStatus.PUBLISHED).pk,
            "price": "150.00",
            "delivery_days": 5,
        },
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert not Offer.objects.exists()


@pytest.mark.django_db
def test_provider_without_approved_kyc_cannot_create_offer(api_client):
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.NOT_SUBMITTED,
    )
    api_client.force_authenticate(provider.user)

    response = api_client.post(
        reverse("api:offer-list"),
        {
            "request": RequestFactory(status=Request.RequestStatus.PUBLISHED).pk,
            "price": "150.00",
            "delivery_days": 5,
        },
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert not Offer.objects.exists()


@pytest.mark.django_db
def test_provider_cannot_create_offer_for_unpublished_request(api_client):
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(provider.user)

    response = api_client.post(
        reverse("api:offer-list"),
        {
            "request": RequestFactory(status=Request.RequestStatus.DRAFT).pk,
            "price": "150.00",
            "delivery_days": 5,
        },
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert not Offer.objects.exists()


@pytest.mark.django_db
def test_provider_lists_only_their_own_offers(api_client):
    provider = ProviderProfileFactory()
    own_offer = OfferFactory(provider=provider)
    OfferFactory()
    api_client.force_authenticate(provider.user)

    response = api_client.get(reverse("api:offer-list"))

    assert response.status_code == HTTPStatus.OK
    assert [item["id"] for item in response.data] == [own_offer.pk]


@pytest.mark.django_db
def test_customer_lists_offers_for_their_request(api_client):
    service_request = RequestFactory()
    offers = OfferFactory.create_batch(2, request=service_request)
    OfferFactory()
    api_client.force_authenticate(service_request.customer)

    response = api_client.get(
        reverse("api:offer-for-request"),
        {"request": service_request.pk},
    )

    assert response.status_code == HTTPStatus.OK
    assert {item["id"] for item in response.data} == {offer.pk for offer in offers}


@pytest.mark.django_db
def test_customer_cannot_list_offers_for_another_customers_request(api_client):
    offer = OfferFactory()
    other_request = RequestFactory()
    api_client.force_authenticate(other_request.customer)

    response = api_client.get(
        reverse("api:offer-for-request"),
        {"request": offer.request_id},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.data == []


@pytest.mark.django_db
def test_customer_offer_list_requires_request_id(api_client):
    api_client.force_authenticate(RequestFactory().customer)

    response = api_client.get(reverse("api:offer-for-request"))

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "request" in response.data


@pytest.mark.django_db
def test_customer_offer_list_rejects_non_numeric_request_id(api_client):
    api_client.force_authenticate(UserFactory())

    response = api_client.get(
        reverse("api:offer-for-request"),
        {"request": "not-a-number"},
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "request" in response.data


@pytest.mark.django_db
def test_customer_accepts_offer_and_rejects_competing_offers(api_client):
    service_request = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    selected = OfferFactory(request=service_request)
    competing = OfferFactory(request=service_request)
    api_client.force_authenticate(service_request.customer)

    response = api_client.post(reverse("api:offer-accept", args=[selected.pk]))

    assert response.status_code == HTTPStatus.OK
    assert response.data["id"] == selected.pk
    assert response.data["status"] == Offer.OfferStatus.ACCEPTED
    selected.refresh_from_db()
    competing.refresh_from_db()
    service_request.refresh_from_db()
    assert selected.status == Offer.OfferStatus.ACCEPTED
    assert competing.status == Offer.OfferStatus.REJECTED
    assert service_request.status == Request.RequestStatus.CLOSED


@pytest.mark.django_db
def test_customer_cannot_accept_offer_for_another_customers_request(api_client):
    offer = OfferFactory(request__status=Request.RequestStatus.PUBLISHED)
    api_client.force_authenticate(UserFactory())

    response = api_client.post(reverse("api:offer-accept", args=[offer.pk]))

    assert response.status_code == HTTPStatus.NOT_FOUND
    offer.refresh_from_db()
    assert offer.status == Offer.OfferStatus.PENDING


@pytest.mark.django_db
def test_accept_offer_with_non_numeric_id_returns_not_found(api_client):
    api_client.force_authenticate(UserFactory())
    numeric_url = reverse("api:offer-accept", args=[1])
    invalid_url = numeric_url.replace("/1/", "/not-a-number/")

    response = api_client.post(invalid_url)

    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
def test_customer_cannot_accept_an_offer_twice(api_client):
    service_request = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    offer = OfferFactory(request=service_request)
    api_client.force_authenticate(service_request.customer)
    url = reverse("api:offer-accept", args=[offer.pk])

    assert api_client.post(url).status_code == HTTPStatus.OK
    response = api_client.post(url)

    assert response.status_code == HTTPStatus.CONFLICT
