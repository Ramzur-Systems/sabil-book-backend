from http import HTTPStatus

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from sabil_book.users.tests.factories import UserFactory

from .models import NotificationPreference


@pytest.mark.django_db
def test_user_can_disable_non_critical_notifications():
    user = UserFactory.create()
    client = APIClient()
    client.force_authenticate(user)
    url = reverse("api:notification-settings-me")

    initial_response = client.get(url)
    update_response = client.patch(
        url,
        {"receive_non_critical": False},
        format="json",
    )

    assert initial_response.status_code == HTTPStatus.OK
    assert initial_response.data["receive_non_critical"] is True
    assert update_response.status_code == HTTPStatus.OK
    assert update_response.data["receive_non_critical"] is False
    assert not NotificationPreference.objects.get(user=user).receive_non_critical


@pytest.mark.django_db
def test_notification_settings_require_authentication():
    response = APIClient().get(reverse("api:notification-settings-me"))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
