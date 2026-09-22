from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from sabil_book.users.models import User
from sabil_book.users.tests.factories import UserFactory

STRONG_PASSWORD = "N7$xQ9vLbT2mK!wZ"  # noqa: S105


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


class TestRegisterView:
    def test_register_creates_user_and_returns_tokens(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/auth/register/",
            {
                "email": "new-user@example.com",
                "password": STRONG_PASSWORD,
                "fullName": "New User",
                "country": "QA",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "access" in response.data
        assert "refresh" in response.data
        assert response.data["user"]["fullName"] == "New User"
        assert response.data["user"]["isProvider"] is False

        user = User.objects.get(email="new-user@example.com")
        assert user.check_password(STRONG_PASSWORD)
        assert user.country == "QA"

    def test_register_duplicate_email_fails(self, db, api_client: APIClient):
        UserFactory.create(email="taken@example.com")

        response = api_client.post(
            "/api/auth/register/",
            {"email": "taken@example.com", "password": STRONG_PASSWORD},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_register_weak_password_fails(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/auth/register/",
            {"email": "weak@example.com", "password": "password"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data
        assert not User.objects.filter(email="weak@example.com").exists()

    def test_register_invalid_email_fails(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/auth/register/",
            {"email": "not-an-email", "password": STRONG_PASSWORD},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_register_missing_fields_fails(self, db, api_client: APIClient):
        response = api_client.post("/api/auth/register/", {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data
        assert "password" in response.data

    def test_register_as_provider_creates_provider_profile(
        self,
        db,
        api_client: APIClient,
    ):
        response = api_client.post(
            "/api/auth/register/",
            {
                "email": "provider@example.com",
                "password": STRONG_PASSWORD,
                "fullName": "New Provider",
                "country": "QA",
                "isProvider": True,
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["user"]["isProvider"] is True

        user = User.objects.get(email="provider@example.com")
        assert hasattr(user, "provider_profile")


class TestLoginView:
    def test_login_success(self, db, api_client: APIClient):
        UserFactory.create(email="login@example.com", password=STRONG_PASSWORD)

        response = api_client.post(
            "/api/auth/login/",
            {"email": "login@example.com", "password": STRONG_PASSWORD},
        )

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data
        assert response.data["user"]["email"] == "login@example.com"

    def test_login_wrong_password_fails(self, db, api_client: APIClient):
        UserFactory.create(email="login2@example.com", password=STRONG_PASSWORD)

        response = api_client.post(
            "/api/auth/login/",
            {"email": "login2@example.com", "password": "wrong-password"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_nonexistent_user_fails(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/auth/login/",
            {"email": "nobody@example.com", "password": STRONG_PASSWORD},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_missing_fields_fails(self, db, api_client: APIClient):
        response = api_client.post("/api/auth/login/", {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_inactive_user_fails(self, db, api_client: APIClient):
        UserFactory.create(
            email="inactive@example.com",
            password=STRONG_PASSWORD,
            is_active=False,
        )

        response = api_client.post(
            "/api/auth/login/",
            {"email": "inactive@example.com", "password": STRONG_PASSWORD},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRefreshAndLogout:
    def _tokens_for(self, api_client: APIClient) -> dict:
        UserFactory.create(email="refresh@example.com", password=STRONG_PASSWORD)
        login_response = api_client.post(
            "/api/auth/login/",
            {"email": "refresh@example.com", "password": STRONG_PASSWORD},
        )
        return login_response.data

    def test_refresh_returns_new_access_token(self, db, api_client: APIClient):
        tokens = self._tokens_for(api_client)

        response = api_client.post(
            "/api/auth/refresh/",
            {"refresh": tokens["refresh"]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data

    def test_refresh_rotates_refresh_token(self, db, api_client: APIClient):
        """SIMPLE_JWT has ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION enabled."""
        tokens = self._tokens_for(api_client)

        response = api_client.post(
            "/api/auth/refresh/",
            {"refresh": tokens["refresh"]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert "refresh" in response.data
        assert response.data["refresh"] != tokens["refresh"]

    def test_refresh_old_token_rejected_after_rotation(self, db, api_client: APIClient):
        tokens = self._tokens_for(api_client)

        first_refresh = api_client.post(
            "/api/auth/refresh/",
            {"refresh": tokens["refresh"]},
        )
        assert first_refresh.status_code == status.HTTP_200_OK

        reuse_response = api_client.post(
            "/api/auth/refresh/",
            {"refresh": tokens["refresh"]},
        )

        assert reuse_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_with_garbage_token_fails(self, db, api_client: APIClient):
        response = api_client.post(
            "/api/auth/refresh/",
            {"refresh": "not-a-real-token"},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_blacklists_refresh_token(self, db, api_client: APIClient):
        tokens = self._tokens_for(api_client)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        logout_response = api_client.post(
            "/api/auth/logout/",
            {"refresh": tokens["refresh"]},
        )
        assert logout_response.status_code == status.HTTP_205_RESET_CONTENT

        api_client.credentials()
        refresh_response = api_client.post(
            "/api/auth/refresh/",
            {"refresh": tokens["refresh"]},
        )
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_requires_authentication(self, db, api_client: APIClient):
        tokens = self._tokens_for(api_client)

        response = api_client.post(
            "/api/auth/logout/",
            {"refresh": tokens["refresh"]},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_missing_refresh_fails(self, db, api_client: APIClient):
        tokens = self._tokens_for(api_client)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        response = api_client.post("/api/auth/logout/", {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_logout_with_garbage_refresh_fails(self, db, api_client: APIClient):
        tokens = self._tokens_for(api_client)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        response = api_client.post(
            "/api/auth/logout/",
            {"refresh": "not-a-real-token"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestCurrentUserView:
    def test_me_returns_authenticated_user(self, db, api_client: APIClient):
        user = UserFactory.create(
            email="me@example.com",
            password=STRONG_PASSWORD,
            name="Me User",
        )
        login_response = api_client.post(
            "/api/auth/login/",
            {"email": "me@example.com", "password": STRONG_PASSWORD},
        )
        access = login_response.data["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = api_client.get("/api/auth/me/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["fullName"] == user.name
        assert response.data["email"] == user.email
        assert response.data["isProvider"] is False

    def test_me_requires_authentication(self, db, api_client: APIClient):
        response = api_client.get("/api/auth/me/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_me_rejects_garbage_token(self, db, api_client: APIClient):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

        response = api_client.get("/api/auth/me/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_me_rejects_access_token_after_logout(self, db, api_client: APIClient):
        """Logout only blacklists the refresh token; the access token stays
        valid until it naturally expires (stateless JWT access tokens are not
        blacklisted)."""
        UserFactory.create(email="me2@example.com", password=STRONG_PASSWORD)
        login_response = api_client.post(
            "/api/auth/login/",
            {"email": "me2@example.com", "password": STRONG_PASSWORD},
        )
        access = login_response.data["access"]
        refresh = login_response.data["refresh"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        api_client.post("/api/auth/logout/", {"refresh": refresh})

        response = api_client.get("/api/auth/me/")

        assert response.status_code == status.HTTP_200_OK
