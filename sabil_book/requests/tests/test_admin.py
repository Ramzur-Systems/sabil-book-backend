from http import HTTPStatus

import pytest
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.urls import reverse

from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.requests.models import Request
from sabil_book.requests.tests.factories import RequestFactory

pytestmark = pytest.mark.django_db


def _post_action(client, action, pks, **extra):
    return client.post(
        reverse("admin:requests_request_changelist"),
        data={"action": action, ACTION_CHECKBOX_NAME: pks, **extra},
    )


def test_admin_lists_requests_offers_and_users(admin_client):
    OfferFactory()
    for name in (
        "admin:requests_request_changelist",
        "admin:offers_offer_changelist",
        "admin:users_user_changelist",
    ):
        assert admin_client.get(reverse(name)).status_code == HTTPStatus.OK


def test_request_change_page_renders_readonly(admin_client):
    obj = RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    url = reverse("admin:requests_request_change", args=[obj.pk])
    assert admin_client.get(url).status_code == HTTPStatus.OK


def test_approve_action_publishes(admin_client):
    obj = RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    response = _post_action(admin_client, "approve_requests", [obj.pk])
    assert response.status_code == HTTPStatus.FOUND
    obj.refresh_from_db()
    assert obj.status == Request.RequestStatus.PUBLISHED
    assert obj.published_at is not None


def test_reject_action_shows_reason_form_first(admin_client):
    obj = RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    response = _post_action(admin_client, "reject_requests", [obj.pk])
    assert response.status_code == HTTPStatus.OK
    obj.refresh_from_db()
    assert obj.status == Request.RequestStatus.MANUAL_REVIEW


def test_reject_action_requires_reason(admin_client):
    obj = RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    response = _post_action(admin_client, "reject_requests", [obj.pk], apply="1")
    assert response.status_code == HTTPStatus.OK
    obj.refresh_from_db()
    assert obj.status == Request.RequestStatus.MANUAL_REVIEW


def test_reject_action_with_reason(admin_client):
    obj = RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    response = _post_action(
        admin_client,
        "reject_requests",
        [obj.pk],
        apply="1",
        reason="Not allowed",
    )
    assert response.status_code == HTTPStatus.FOUND
    obj.refresh_from_db()
    assert obj.status == Request.RequestStatus.REJECTED
    assert obj.rejection_reason == "Not allowed"


def test_actions_skip_requests_not_in_review(admin_client):
    obj = RequestFactory(status=Request.RequestStatus.PUBLISHED)
    _post_action(admin_client, "approve_requests", [obj.pk])
    obj.refresh_from_db()
    assert obj.status == Request.RequestStatus.PUBLISHED
    assert obj.rejection_reason == ""


def test_non_staff_cannot_moderate(client, django_user_model):
    user = django_user_model.objects.create_user(email="u@example.com", password="x")  # noqa: S106
    client.force_login(user)
    obj = RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    _post_action(client, "approve_requests", [obj.pk])
    obj.refresh_from_db()
    assert obj.status == Request.RequestStatus.MANUAL_REVIEW


def test_index_shows_moderation_count(admin_client):
    RequestFactory(status=Request.RequestStatus.MANUAL_REVIEW)
    response = admin_client.get(reverse("admin:index"))
    assert response.status_code == HTTPStatus.OK
    assert b"Requests awaiting manual review" in response.content
