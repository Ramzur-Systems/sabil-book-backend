from http import HTTPStatus

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from sabil_book.notifications.events import NotificationEvent
from sabil_book.notifications.models import NotificationDelivery
from sabil_book.offers.models import Offer
from sabil_book.offers.tests.factories import MessageFactory
from sabil_book.requests.models import Request
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.models import ProviderProfile
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def request_payload() -> dict:
    return {
        "category": Request.RequestCategory.RESEARCH_BRIEF,
        "title": "Direct market research",
        "description": "Research this market and summarize the important findings.",
        "budget": "250.00",
        "deadline": "2027-01-15",
    }


def create_direct_request(api_client, customer, provider, payload):
    api_client.force_authenticate(customer)
    return api_client.post(
        reverse("api:provider-direct-requests", args=[provider.pk]),
        payload,
    )


@pytest.mark.django_db
def test_client_creates_direct_request_for_approved_provider(
    api_client,
    request_payload,
):
    customer = UserFactory()
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )

    response = create_direct_request(api_client, customer, provider, request_payload)

    assert response.status_code == HTTPStatus.CREATED
    assert response.data["visibility"] == Request.RequestVisibility.DIRECT
    assert response.data["selected_provider"] == provider.pk
    assert response.data["direct_status"] == Request.DirectRequestStatus.PENDING
    assert response.data["deadline"] == "2027-01-15"
    service_request = Request.objects.get(pk=response.data["id"])
    assert service_request.customer == customer
    assert service_request.status == Request.RequestStatus.DRAFT


@pytest.mark.django_db
def test_public_request_creation_remains_public(api_client, request_payload):
    customer = UserFactory()
    api_client.force_authenticate(customer)

    response = api_client.post(reverse("api:request-list"), request_payload)

    assert response.status_code == HTTPStatus.CREATED
    assert response.data["visibility"] == Request.RequestVisibility.PUBLIC
    assert response.data["selected_provider"] is None
    assert response.data["direct_status"] is None


@pytest.mark.django_db
def test_client_cannot_target_self_or_unapproved_provider(
    api_client,
    request_payload,
):
    customer = UserFactory()
    own_profile = ProviderProfileFactory(
        user=customer,
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    unapproved = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.PENDING,
    )

    self_response = create_direct_request(
        api_client,
        customer,
        own_profile,
        request_payload,
    )
    unapproved_response = create_direct_request(
        api_client,
        customer,
        unapproved,
        request_payload,
    )

    assert self_response.status_code == HTTPStatus.BAD_REQUEST
    assert unapproved_response.status_code == HTTPStatus.BAD_REQUEST
    assert not Request.objects.exists()


@pytest.mark.django_db
def test_submitted_direct_request_is_visible_only_to_participants(
    api_client,
    request_payload,
):
    customer = UserFactory()
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    stranger = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    admin = UserFactory(is_staff=True)
    create_response = create_direct_request(
        api_client,
        customer,
        provider,
        request_payload,
    )
    request_id = create_response.data["id"]
    submit_response = api_client.post(
        reverse("api:request-submit", args=[request_id]),
    )

    assert submit_response.status_code == HTTPStatus.OK
    assert submit_response.data["status"] == Request.RequestStatus.PUBLISHED
    delivery = NotificationDelivery.objects.get(
        event=NotificationEvent.DIRECT_REQUEST_RECEIVED,
    )
    assert delivery.recipient == provider.user

    api_client.force_authenticate(provider.user)
    assert (
        api_client.get(reverse("api:request-detail", args=[request_id])).status_code
        == HTTPStatus.OK
    )
    assigned_ids = [
        item["id"] for item in api_client.get(reverse("api:request-list")).data
    ]
    assert assigned_ids == [request_id]

    api_client.force_authenticate(stranger.user)
    assert (
        api_client.get(reverse("api:request-detail", args=[request_id])).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert (
        api_client.get(reverse("api:browse-detail", args=[request_id])).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert api_client.get(reverse("api:request-list")).data == []
    assert api_client.get(reverse("api:browse-list")).data == []

    # Staff review requests through the moderation endpoints, not this one.
    api_client.force_authenticate(admin)
    assert (
        api_client.get(reverse("api:request-detail", args=[request_id])).status_code
        == HTTPStatus.NOT_FOUND
    )


@pytest.mark.django_db
def test_selected_provider_cannot_mutate_direct_request(api_client):
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    service_request = RequestFactory(
        direct=True,
        selected_provider=provider,
        status=Request.RequestStatus.PUBLISHED,
    )
    api_client.force_authenticate(provider.user)

    response = api_client.patch(
        reverse("api:request-detail", args=[service_request.pk]),
        {"title": "Provider edit"},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    service_request.refresh_from_db()
    assert service_request.title != "Provider edit"


@pytest.mark.django_db
def test_selected_provider_accepts_direct_request_and_client_is_notified(api_client):
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    service_request = RequestFactory(
        direct=True,
        selected_provider=provider,
        status=Request.RequestStatus.PUBLISHED,
    )
    api_client.force_authenticate(provider.user)
    url = reverse("api:request-accept", args=[service_request.pk])

    response = api_client.post(url)

    assert response.status_code == HTTPStatus.OK
    assert response.data["direct_status"] == Request.DirectRequestStatus.ACCEPTED
    delivery = NotificationDelivery.objects.get(
        event=NotificationEvent.DIRECT_REQUEST_ACCEPTED,
    )
    assert delivery.recipient == service_request.customer
    assert api_client.post(url).status_code == HTTPStatus.CONFLICT


@pytest.mark.django_db
def test_unrelated_provider_cannot_accept_or_offer_on_direct_request(api_client):
    selected_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    stranger = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    service_request = RequestFactory(
        direct=True,
        selected_provider=selected_provider,
        direct_status=Request.DirectRequestStatus.ACCEPTED,
        status=Request.RequestStatus.PUBLISHED,
    )
    api_client.force_authenticate(stranger.user)

    accept_response = api_client.post(
        reverse("api:request-accept", args=[service_request.pk]),
    )
    offer_response = api_client.post(
        reverse("api:offer-list"),
        {"request": service_request.pk, "price": "100.00", "delivery_days": 3},
    )

    assert accept_response.status_code == HTTPStatus.NOT_FOUND
    assert offer_response.status_code == HTTPStatus.BAD_REQUEST
    assert not Offer.objects.exists()


@pytest.mark.django_db
def test_selected_provider_declines_and_cannot_submit_offer(api_client):
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    service_request = RequestFactory(
        direct=True,
        selected_provider=provider,
        status=Request.RequestStatus.PUBLISHED,
    )
    api_client.force_authenticate(provider.user)

    decline_response = api_client.post(
        reverse("api:request-decline", args=[service_request.pk]),
    )
    offer_response = api_client.post(
        reverse("api:offer-list"),
        {"request": service_request.pk, "price": "100.00", "delivery_days": 3},
    )

    assert decline_response.status_code == HTTPStatus.OK
    assert (
        decline_response.data["direct_status"] == Request.DirectRequestStatus.DECLINED
    )
    assert NotificationDelivery.objects.filter(
        recipient=service_request.customer,
        event=NotificationEvent.DIRECT_REQUEST_DECLINED,
    ).exists()
    assert offer_response.status_code == HTTPStatus.BAD_REQUEST
    assert not Offer.objects.exists()


@pytest.mark.django_db
def test_selected_provider_can_offer_after_accepting(api_client):
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    service_request = RequestFactory(
        direct=True,
        selected_provider=provider,
        status=Request.RequestStatus.PUBLISHED,
    )
    api_client.force_authenticate(provider.user)
    api_client.post(reverse("api:request-accept", args=[service_request.pk]))

    response = api_client.post(
        reverse("api:offer-list"),
        {"request": service_request.pk, "price": "100.00", "delivery_days": 3},
    )

    assert response.status_code == HTTPStatus.CREATED
    assert Offer.objects.get().provider == provider


@pytest.mark.django_db
def test_direct_request_notification_waits_for_moderation_approval(
    api_client,
    request_payload,
    settings,
):
    settings.REQUEST_MODERATION_BLOCKLIST = ["important findings"]
    customer = UserFactory()
    provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    create_response = create_direct_request(
        api_client,
        customer,
        provider,
        request_payload,
    )
    request_id = create_response.data["id"]

    submit_response = api_client.post(reverse("api:request-submit", args=[request_id]))

    assert submit_response.data["status"] == Request.RequestStatus.MANUAL_REVIEW
    assert not NotificationDelivery.objects.filter(
        event=NotificationEvent.DIRECT_REQUEST_RECEIVED,
    ).exists()

    api_client.force_authenticate(UserFactory(is_staff=True))
    approve_response = api_client.post(
        reverse("api:request-moderation-approve", args=[request_id]),
    )

    assert approve_response.status_code == HTTPStatus.OK
    assert (
        NotificationDelivery.objects.filter(
            recipient=provider.user,
            event=NotificationEvent.DIRECT_REQUEST_RECEIVED,
        ).count()
        == 1
    )


def declined_direct_request(**kwargs) -> Request:
    return RequestFactory(
        direct=True,
        direct_status=Request.DirectRequestStatus.DECLINED,
        status=Request.RequestStatus.PUBLISHED,
        **kwargs,
    )


@pytest.mark.django_db
def test_draft_direct_request_is_hidden_from_selected_provider(api_client):
    service_request = RequestFactory(direct=True)
    api_client.force_authenticate(service_request.selected_provider.user)

    detail = api_client.get(reverse("api:request-detail", args=[service_request.pk]))
    accept = api_client.post(reverse("api:request-accept", args=[service_request.pk]))

    assert detail.status_code == HTTPStatus.NOT_FOUND
    assert accept.status_code == HTTPStatus.NOT_FOUND
    assert api_client.get(reverse("api:request-list")).data == []


@pytest.mark.django_db
def test_staff_request_list_still_shows_only_own_requests(api_client):
    admin = UserFactory(is_staff=True)
    own = RequestFactory(customer=admin)
    RequestFactory(direct=True, status=Request.RequestStatus.PUBLISHED)
    api_client.force_authenticate(admin)

    response = api_client.get(reverse("api:request-list"))

    assert [item["id"] for item in response.data] == [own.pk]


@pytest.mark.django_db
def test_client_sees_declined_status(api_client):
    service_request = declined_direct_request()
    api_client.force_authenticate(service_request.customer)

    response = api_client.get(reverse("api:request-detail", args=[service_request.pk]))

    assert response.status_code == HTTPStatus.OK
    assert response.data["direct_status"] == Request.DirectRequestStatus.DECLINED


@pytest.mark.django_db
def test_client_reassigns_declined_request_to_another_provider(api_client):
    service_request = declined_direct_request()
    previous_provider = service_request.selected_provider
    new_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(service_request.customer)

    response = api_client.post(
        reverse("api:request-reassign", args=[service_request.pk]),
        {"provider": new_provider.pk},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.data["selected_provider"] == new_provider.pk
    assert response.data["direct_status"] == Request.DirectRequestStatus.PENDING
    assert response.data["status"] == Request.RequestStatus.PUBLISHED
    assert NotificationDelivery.objects.filter(
        recipient=new_provider.user,
        event=NotificationEvent.DIRECT_REQUEST_RECEIVED,
    ).exists()

    detail_url = reverse("api:request-detail", args=[service_request.pk])
    api_client.force_authenticate(previous_provider.user)
    assert api_client.get(detail_url).status_code == HTTPStatus.NOT_FOUND
    api_client.force_authenticate(new_provider.user)
    assert api_client.get(detail_url).status_code == HTTPStatus.OK


@pytest.mark.django_db
def test_reassign_rejects_invalid_targets_and_states(api_client):
    service_request = declined_direct_request(
        selected_provider=ProviderProfileFactory(
            kyc_status=ProviderProfile.KYCStatus.APPROVED,
        ),
    )
    customer = service_request.customer
    url = reverse("api:request-reassign", args=[service_request.pk])
    api_client.force_authenticate(customer)

    same_provider = api_client.post(
        url,
        {"provider": service_request.selected_provider.pk},
    )
    unapproved = api_client.post(
        url,
        {
            "provider": ProviderProfileFactory(
                kyc_status=ProviderProfile.KYCStatus.PENDING,
            ).pk,
        },
    )
    own_profile = api_client.post(
        url,
        {
            "provider": ProviderProfileFactory(
                user=customer,
                kyc_status=ProviderProfile.KYCStatus.APPROVED,
            ).pk,
        },
    )
    pending_request = RequestFactory(
        direct=True,
        customer=customer,
        status=Request.RequestStatus.PUBLISHED,
    )
    not_declined = api_client.post(
        reverse("api:request-reassign", args=[pending_request.pk]),
        {
            "provider": ProviderProfileFactory(
                kyc_status=ProviderProfile.KYCStatus.APPROVED,
            ).pk,
        },
    )

    assert same_provider.status_code == HTTPStatus.CONFLICT
    assert unapproved.status_code == HTTPStatus.BAD_REQUEST
    assert own_profile.status_code == HTTPStatus.BAD_REQUEST
    assert not_declined.status_code == HTTPStatus.CONFLICT
    service_request.refresh_from_db()
    assert service_request.direct_status == Request.DirectRequestStatus.DECLINED


@pytest.mark.django_db
def test_only_the_client_can_reassign_or_make_public(api_client):
    service_request = declined_direct_request()
    new_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    reassign_url = reverse("api:request-reassign", args=[service_request.pk])
    public_url = reverse("api:request-make-public", args=[service_request.pk])

    api_client.force_authenticate(service_request.selected_provider.user)
    assert (
        api_client.post(reassign_url, {"provider": new_provider.pk}).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert api_client.post(public_url).status_code == HTTPStatus.FORBIDDEN

    api_client.force_authenticate(new_provider.user)
    assert (
        api_client.post(reassign_url, {"provider": new_provider.pk}).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert api_client.post(public_url).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
def test_client_converts_declined_request_to_public(api_client):
    service_request = declined_direct_request()
    other_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(service_request.customer)

    response = api_client.post(
        reverse("api:request-make-public", args=[service_request.pk]),
    )

    assert response.status_code == HTTPStatus.OK
    assert response.data["visibility"] == Request.RequestVisibility.PUBLIC
    assert response.data["selected_provider"] is None
    assert response.data["direct_status"] is None

    api_client.force_authenticate(other_provider.user)
    browse_ids = [
        item["id"] for item in api_client.get(reverse("api:browse-list")).data
    ]
    assert browse_ids == [service_request.pk]
    offer_response = api_client.post(
        reverse("api:offer-list"),
        {"request": service_request.pk, "price": "100.00", "delivery_days": 3},
    )
    assert offer_response.status_code == HTTPStatus.CREATED


@pytest.mark.django_db
def test_pending_direct_request_cannot_be_made_public(api_client):
    service_request = RequestFactory(
        direct=True,
        status=Request.RequestStatus.PUBLISHED,
    )
    api_client.force_authenticate(service_request.customer)

    response = api_client.post(
        reverse("api:request-make-public", args=[service_request.pk]),
    )

    assert response.status_code == HTTPStatus.CONFLICT
    service_request.refresh_from_db()
    assert service_request.visibility == Request.RequestVisibility.DIRECT


@pytest.mark.django_db
def test_editing_declined_request_returns_it_to_draft_for_resubmission(api_client):
    service_request = declined_direct_request(
        selected_provider=ProviderProfileFactory(
            kyc_status=ProviderProfile.KYCStatus.APPROVED,
        ),
    )
    provider = service_request.selected_provider
    api_client.force_authenticate(service_request.customer)

    edit_response = api_client.patch(
        reverse("api:request-detail", args=[service_request.pk]),
        {"description": "A clearer scope for the research brief."},
    )

    assert edit_response.status_code == HTTPStatus.OK
    assert edit_response.data["status"] == Request.RequestStatus.DRAFT
    assert edit_response.data["direct_status"] == Request.DirectRequestStatus.DECLINED
    assert edit_response.data["published_at"] is None

    submit_response = api_client.post(
        reverse("api:request-submit", args=[service_request.pk]),
    )

    assert submit_response.status_code == HTTPStatus.OK
    assert submit_response.data["status"] == Request.RequestStatus.PUBLISHED
    assert NotificationDelivery.objects.filter(
        recipient=provider.user,
        event=NotificationEvent.DIRECT_REQUEST_RECEIVED,
    ).exists()


@pytest.mark.django_db
def test_reassigned_provider_does_not_see_previous_conversation(api_client):
    service_request = declined_direct_request()
    previous_provider = service_request.selected_provider
    MessageFactory(
        offer=None,
        request=service_request,
        provider=previous_provider,
        sender=service_request.customer,
    )
    new_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(service_request.customer)
    api_client.post(
        reverse("api:request-reassign", args=[service_request.pk]),
        {"provider": new_provider.pk},
    )
    url = reverse("api:direct-request-messages", args=[service_request.pk])

    api_client.force_authenticate(new_provider.user)
    assert api_client.get(url).data["results"] == []
    api_client.force_authenticate(previous_provider.user)
    assert api_client.get(url).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
def test_edited_declined_request_can_still_be_reassigned(api_client):
    service_request = declined_direct_request()
    new_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(service_request.customer)
    api_client.patch(
        reverse("api:request-detail", args=[service_request.pk]),
        {"description": "A clearer scope for the research brief."},
    )

    response = api_client.post(
        reverse("api:request-reassign", args=[service_request.pk]),
        {"provider": new_provider.pk},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.data["status"] == Request.RequestStatus.PUBLISHED
    assert response.data["direct_status"] == Request.DirectRequestStatus.PENDING


@pytest.mark.django_db
def test_cannot_reassign_back_to_a_provider_who_declined(api_client):
    service_request = declined_direct_request()
    first_provider = service_request.selected_provider
    first_provider.kyc_status = ProviderProfile.KYCStatus.APPROVED
    first_provider.save(update_fields=["kyc_status"])
    # The factory builds the declined state directly, so record the history.
    service_request.declined_providers.add(first_provider)
    second_provider = ProviderProfileFactory(
        kyc_status=ProviderProfile.KYCStatus.APPROVED,
    )
    api_client.force_authenticate(service_request.customer)
    api_client.post(
        reverse("api:request-reassign", args=[service_request.pk]),
        {"provider": second_provider.pk},
    )
    api_client.force_authenticate(second_provider.user)
    api_client.post(reverse("api:request-decline", args=[service_request.pk]))
    api_client.force_authenticate(service_request.customer)

    response = api_client.post(
        reverse("api:request-reassign", args=[service_request.pk]),
        {"provider": first_provider.pk},
    )

    assert response.status_code == HTTPStatus.CONFLICT


@pytest.mark.django_db
def test_selected_provider_does_not_see_moderation_details(api_client):
    service_request = declined_direct_request()
    service_request.moderation_flags = ["flagged"]
    service_request.save(update_fields=["moderation_flags"])
    service_request.direct_status = Request.DirectRequestStatus.PENDING
    service_request.save(update_fields=["direct_status"])
    api_client.force_authenticate(service_request.selected_provider.user)

    response = api_client.get(
        reverse("api:request-detail", args=[service_request.pk]),
    )

    assert response.status_code == HTTPStatus.OK
    assert "moderation_flags" not in response.data
    assert "rejection_reason" not in response.data


@pytest.mark.django_db
def test_past_deadline_is_rejected(api_client, request_payload):
    customer = UserFactory()
    api_client.force_authenticate(customer)

    response = api_client.post(
        reverse("api:request-list"),
        {**request_payload, "deadline": "2000-01-01"},
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
