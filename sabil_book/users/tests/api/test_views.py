from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.test import APIRequestFactory

from sabil_book.users.views import UserViewSet

if TYPE_CHECKING:
    from sabil_book.users.models import User


class TestUserViewSet:
    @pytest.fixture
    def api_rf(self) -> APIRequestFactory:
        return APIRequestFactory()

    def test_get_queryset(self, user: User, api_rf: APIRequestFactory):
        view = UserViewSet()
        request = api_rf.get("/fake-url/")
        request.user = user

        view.request = request

        assert user in view.get_queryset()

    def test_me(self, user: User, api_rf: APIRequestFactory):
        view = UserViewSet()
        request = api_rf.get("/fake-url/")
        request.user = user

        view.request = request

        response = view.me(request)  # type: ignore[misc,call-arg,arg-type]

        assert response.data == {
            "id": str(user.pk),
            "email": user.email,
            "fullName": user.name,
            "isProvider": False,
            "isAdmin": user.is_staff,
            "country": user.country,
            "preferredLanguage": user.preferred_language,
        }


class TestCurrentUserEndpoint:
    """HTTP-level tests for GET/PATCH /api/users/me/."""

    @pytest.fixture
    def api_client(self) -> APIClient:
        return APIClient()

    def _authenticated_client(self, api_client: APIClient, user: User) -> APIClient:
        api_client.force_authenticate(user=user)
        return api_client

    def test_get_me(self, db, user: User, api_client: APIClient):
        self._authenticated_client(api_client, user)

        response = api_client.get("/api/users/me/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["fullName"] == user.name
        assert response.data["country"] == user.country

    def test_get_me_requires_authentication(self, db, api_client: APIClient):
        response = api_client.get("/api/users/me/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_patch_me_updates_own_profile(self, db, user: User, api_client: APIClient):
        self._authenticated_client(api_client, user)

        response = api_client.patch(
            "/api/users/me/",
            {"fullName": "New Name", "country": "QA"},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["fullName"] == "New Name"
        assert response.data["country"] == "QA"

        user.refresh_from_db()
        assert user.name == "New Name"
        assert user.country == "QA"

    def test_patch_me_requires_authentication(self, db, api_client: APIClient):
        response = api_client.patch("/api/users/me/", {"name": "New Name"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
