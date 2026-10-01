from __future__ import annotations

import pytest
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from sabil_book.users.models import ProviderProfile
from sabil_book.users.tests.factories import ProviderProfileFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


class TestKYCWebhook:
    def test_approves_provider(self, db, api_client: APIClient):
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.PENDING,
        )

        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": profile.pk,
                "verification_id": "vrf_123",
                "status": ProviderProfile.KYCStatus.APPROVED,
            },
            HTTP_X_WEBHOOK_SECRET=settings.KYC_WEBHOOK_SECRET,
        )

        assert response.status_code == status.HTTP_200_OK
        profile.refresh_from_db()
        assert profile.kyc_status == ProviderProfile.KYCStatus.APPROVED

    def test_rejects_provider(self, db, api_client: APIClient):
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.PENDING,
        )

        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": profile.pk,
                "status": ProviderProfile.KYCStatus.REJECTED,
            },
            HTTP_X_WEBHOOK_SECRET=settings.KYC_WEBHOOK_SECRET,
        )

        assert response.status_code == status.HTTP_200_OK
        profile.refresh_from_db()
        assert profile.kyc_status == ProviderProfile.KYCStatus.REJECTED

    def test_wrong_secret_is_rejected(self, db, api_client: APIClient):
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.PENDING,
        )

        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": profile.pk,
                "status": ProviderProfile.KYCStatus.APPROVED,
            },
            HTTP_X_WEBHOOK_SECRET="wrong-secret",  # noqa: S106
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        profile.refresh_from_db()
        assert profile.kyc_status == ProviderProfile.KYCStatus.PENDING

    def test_missing_secret_is_rejected(self, db, api_client: APIClient):
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.PENDING,
        )

        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": profile.pk,
                "status": ProviderProfile.KYCStatus.APPROVED,
            },
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_status_is_rejected(self, db, api_client: APIClient):
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.PENDING,
        )

        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": profile.pk,
                "status": "not-a-real-status",
            },
            HTTP_X_WEBHOOK_SECRET=settings.KYC_WEBHOOK_SECRET,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        profile.refresh_from_db()
        assert profile.kyc_status == ProviderProfile.KYCStatus.PENDING

    def test_unknown_provider_returns_404(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": 999999,
                "status": ProviderProfile.KYCStatus.APPROVED,
            },
            HTTP_X_WEBHOOK_SECRET=settings.KYC_WEBHOOK_SECRET,
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_does_not_require_user_authentication(self, db, api_client: APIClient):
        """The caller is the external KYC provider, not a logged-in user."""
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.PENDING,
        )

        response = api_client.post(
            "/api/webhooks/kyc/",
            {
                "provider_profile_id": profile.pk,
                "status": ProviderProfile.KYCStatus.APPROVED,
            },
            HTTP_X_WEBHOOK_SECRET=settings.KYC_WEBHOOK_SECRET,
        )

        assert response.status_code != status.HTTP_401_UNAUTHORIZED
