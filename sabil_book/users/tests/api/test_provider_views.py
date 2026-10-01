from __future__ import annotations

from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.reviews.tests.factories import ReviewFactory
from sabil_book.users.models import ProviderProfile
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory

AVERAGE_OF_FOUR_AND_TWO = 3.0


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


class TestBecomeProvider:
    def test_creates_provider_profile(self, db, api_client: APIClient):
        user = UserFactory.create()
        api_client.force_authenticate(user=user)

        response = api_client.post(
            "/api/providers/",
            {"country": "QA", "payout_provider": "stripe"},
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["country"] == "QA"
        assert response.data["payout_provider"] == "stripe"
        assert response.data["kyc_status"] == ProviderProfile.KYCStatus.NOT_SUBMITTED

        profile = ProviderProfile.objects.get(user=user)
        assert profile.country == "QA"
        assert profile.payout_provider == "stripe"

    def test_requires_authentication(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/providers/",
            {"country": "QA", "payout_provider": "stripe"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_rejects_duplicate_profile(self, db, api_client: APIClient):
        user = UserFactory.create()
        ProviderProfileFactory.create(user=user)
        api_client.force_authenticate(user=user)

        response = api_client.post(
            "/api/providers/",
            {"country": "QA", "payout_provider": "stripe"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_set_kyc_status_directly(self, db, api_client: APIClient):
        user = UserFactory.create()
        api_client.force_authenticate(user=user)

        response = api_client.post(
            "/api/providers/",
            {"country": "QA", "kyc_status": ProviderProfile.KYCStatus.APPROVED},
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["kyc_status"] == ProviderProfile.KYCStatus.NOT_SUBMITTED


class TestPublicProviderProfile:
    def test_returns_public_fields_without_payout_provider(
        self,
        db,
        api_client: APIClient,
    ):
        provider = ProviderProfileFactory.create(
            country="QA",
            payout_provider="secret-stripe-account",
        )

        response = api_client.get(f"/api/providers/{provider.pk}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == provider.pk
        assert response.data["country"] == "QA"
        assert "payout_provider" not in response.data

    def test_does_not_require_authentication(self, db, api_client: APIClient):
        provider = ProviderProfileFactory.create()

        response = api_client.get(f"/api/providers/{provider.pk}/")

        assert response.status_code == status.HTTP_200_OK

    def test_rating_is_none_without_reviews(self, db, api_client: APIClient):
        provider = ProviderProfileFactory.create()

        response = api_client.get(f"/api/providers/{provider.pk}/")

        assert response.data["rating"] is None

    def test_rating_is_average_of_reviews(self, db, api_client: APIClient):
        provider = ProviderProfileFactory.create()

        first_order = OrderFactory.create(offer=OfferFactory.create(provider=provider))
        ReviewFactory.create(order=first_order, rating=Decimal("4.0"))

        second_order = OrderFactory.create(offer=OfferFactory.create(provider=provider))
        ReviewFactory.create(order=second_order, rating=Decimal("2.0"))

        response = api_client.get(f"/api/providers/{provider.pk}/")

        assert response.data["rating"] == AVERAGE_OF_FOUR_AND_TWO

    def test_unknown_provider_returns_404(self, db, api_client: APIClient):
        response = api_client.get("/api/providers/999999/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestUpdateOwnProviderProfile:
    def test_updates_country_and_payout_provider(self, db, api_client: APIClient):
        user = UserFactory.create()
        ProviderProfileFactory.create(user=user, country="QA")
        api_client.force_authenticate(user=user)

        response = api_client.patch(
            "/api/providers/me/",
            {"country": "AE", "payout_provider": "paypal"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["country"] == "AE"
        assert response.data["payout_provider"] == "paypal"

    def test_cannot_set_kyc_status(self, db, api_client: APIClient):
        user = UserFactory.create()
        ProviderProfileFactory.create(
            user=user,
            kyc_status=ProviderProfile.KYCStatus.NOT_SUBMITTED,
        )
        api_client.force_authenticate(user=user)

        response = api_client.patch(
            "/api/providers/me/",
            {"kyc_status": ProviderProfile.KYCStatus.APPROVED},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["kyc_status"] == ProviderProfile.KYCStatus.NOT_SUBMITTED

    def test_requires_authentication(self, db, api_client: APIClient):
        response = api_client.patch("/api/providers/me/", {"country": "AE"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_requires_existing_provider_profile(self, db, api_client: APIClient):
        user = UserFactory.create()
        api_client.force_authenticate(user=user)

        response = api_client.patch("/api/providers/me/", {"country": "AE"})

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestInitiateKyc:
    def test_moves_status_to_pending(self, db, api_client: APIClient):
        user = UserFactory.create()
        profile = ProviderProfileFactory.create(
            user=user,
            kyc_status=ProviderProfile.KYCStatus.NOT_SUBMITTED,
        )
        api_client.force_authenticate(user=user)

        response = api_client.post("/api/providers/me/kyc/initiate/")

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["kyc_status"] == ProviderProfile.KYCStatus.PENDING
        assert "verification_id" in response.data

        profile.refresh_from_db()
        assert profile.kyc_status == ProviderProfile.KYCStatus.PENDING

    def test_requires_authentication(self, db, api_client: APIClient):
        response = api_client.post("/api/providers/me/kyc/initiate/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_requires_existing_provider_profile(self, db, api_client: APIClient):
        user = UserFactory.create()
        api_client.force_authenticate(user=user)

        response = api_client.post("/api/providers/me/kyc/initiate/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
