from dataclasses import dataclass
from unicodedata import normalize

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.notifications.events import NotificationEvent
from sabil_book.notifications.services import NotificationService
from sabil_book.users.models import ProviderProfile

from .models import Request


@dataclass(frozen=True)
class ModerationResult:
    matched_terms: list[str]

    @property
    def requires_manual_review(self) -> bool:
        return bool(self.matched_terms)


def normalize_for_moderation(value: str) -> str:
    normalized = normalize("NFKC", value).casefold()
    words = "".join(
        character if character.isalnum() else " " for character in normalized
    )
    return " ".join(
        words.split(),
    )


def check_content(request: Request) -> ModerationResult:
    content = normalize_for_moderation(f"{request.title}\n{request.description}")
    padded_content = f" {content} "
    blocklist = getattr(settings, "REQUEST_MODERATION_BLOCKLIST", [])
    normalized_terms = {
        normalized_term
        for term in blocklist
        if isinstance(term, str) and (normalized_term := normalize_for_moderation(term))
    }
    matches = sorted(term for term in normalized_terms if f" {term} " in padded_content)
    return ModerationResult(matched_terms=matches)


def _direct_request_context(request: Request) -> dict[str, str | int]:
    if request.selected_provider is None:
        msg = "A direct request must have a selected provider."
        raise InvalidTransitionError(msg)
    return {
        "request_id": request.pk,
        "request_title": request.title,
        "provider_name": (
            request.selected_provider.user.name or request.selected_provider.user.email
        ),
    }


def _notify_direct_request_submitted(request: Request) -> None:
    if request.visibility != Request.RequestVisibility.DIRECT:
        return
    NotificationService.notify(
        event=NotificationEvent.DIRECT_REQUEST_RECEIVED,
        recipient=request.selected_provider.user,
        context=_direct_request_context(request),
    )


def _validate_direct_request_target(request: Request) -> None:
    if request.visibility != Request.RequestVisibility.DIRECT:
        return
    if request.selected_provider is None:
        msg = "A direct request must have a selected provider."
        raise InvalidTransitionError(msg)
    if request.selected_provider.user_id == request.customer_id:
        msg = "A client cannot send a direct request to their own provider profile."
        raise InvalidTransitionError(msg)
    if request.selected_provider.kyc_status != ProviderProfile.KYCStatus.APPROVED:
        msg = "The selected provider must have approved KYC."
        raise InvalidTransitionError(msg)


_MODERATION_FIELDS = [
    "status",
    "rejection_reason",
    "moderation_flags",
    "published_at",
    "updated_at",
]


def _apply_moderation(request: Request) -> None:
    """Publish the request, or hold it for manual review if content is flagged."""
    request.rejection_reason = ""
    result = check_content(request)
    request.moderation_flags = result.matched_terms
    if result.requires_manual_review:
        request.status = Request.RequestStatus.MANUAL_REVIEW
        request.published_at = None
    else:
        request.status = Request.RequestStatus.PUBLISHED
        request.published_at = timezone.now()


@transaction.atomic
def submit_request(request_id: int) -> Request:
    request = (
        Request.objects.select_for_update()
        .select_related("selected_provider__user")
        .get(pk=request_id)
    )
    if request.status not in {
        Request.RequestStatus.DRAFT,
        Request.RequestStatus.REJECTED,
    }:
        msg = "Only a draft or rejected request can be submitted."
        raise InvalidTransitionError(msg)

    _validate_direct_request_target(request)

    update_fields = list(_MODERATION_FIELDS)
    if request.direct_status == Request.DirectRequestStatus.DECLINED:
        # An edited, previously declined request is being sent again.
        request.direct_status = Request.DirectRequestStatus.PENDING
        update_fields.append("direct_status")
    _apply_moderation(request)
    request.save(update_fields=update_fields)
    if request.status == Request.RequestStatus.PUBLISHED:
        _notify_direct_request_submitted(request)
    return request


@transaction.atomic
def moderate_request(request_id: int, *, approve: bool, reason: str = "") -> Request:
    request = (
        Request.objects.select_for_update()
        .select_related("selected_provider__user")
        .get(pk=request_id)
    )
    if request.status != Request.RequestStatus.MANUAL_REVIEW:
        msg = "Only requests awaiting manual review can be moderated."
        raise InvalidTransitionError(msg)
    if not approve and not reason.strip():
        msg = "A rejection reason is required."
        raise InvalidTransitionError(msg)
    if approve:
        _validate_direct_request_target(request)

    request.status = (
        Request.RequestStatus.PUBLISHED if approve else Request.RequestStatus.REJECTED
    )
    request.rejection_reason = "" if approve else reason.strip()
    request.published_at = timezone.now() if approve else None
    request.save(
        update_fields=["status", "rejection_reason", "published_at", "updated_at"],
    )
    if approve:
        _notify_direct_request_submitted(request)
    return request


@transaction.atomic
def respond_to_direct_request(
    request_id: int,
    provider_user,
    *,
    accept: bool,
) -> Request:
    request = (
        Request.objects.select_for_update()
        .select_related("customer", "selected_provider__user")
        .get(
            pk=request_id,
            visibility=Request.RequestVisibility.DIRECT,
        )
    )
    # Same visibility rule as the request list: the provider only sees the
    # request once it has been submitted and published.
    if request.selected_provider.user_id != provider_user.id or request.status in {
        Request.RequestStatus.DRAFT,
        Request.RequestStatus.MANUAL_REVIEW,
        Request.RequestStatus.REJECTED,
    }:
        raise Request.DoesNotExist
    if request.status != Request.RequestStatus.PUBLISHED:
        msg = "Only a published direct request can be answered."
        raise InvalidTransitionError(msg)
    if request.direct_status != Request.DirectRequestStatus.PENDING:
        msg = "Only a pending direct request can be answered."
        raise InvalidTransitionError(msg)

    request.direct_status = (
        Request.DirectRequestStatus.ACCEPTED
        if accept
        else Request.DirectRequestStatus.DECLINED
    )
    request.save(update_fields=["direct_status", "updated_at"])
    if not accept:
        request.declined_providers.add(request.selected_provider)
    NotificationService.notify(
        event=(
            NotificationEvent.DIRECT_REQUEST_ACCEPTED
            if accept
            else NotificationEvent.DIRECT_REQUEST_DECLINED
        ),
        recipient=request.customer,
        context=_direct_request_context(request),
    )
    return request


def _get_declined_direct_request_for_update(request_id: int, customer) -> Request:
    request = (
        Request.objects.select_for_update()
        .select_related("selected_provider__user")
        .get(
            pk=request_id,
            customer=customer,
            visibility=Request.RequestVisibility.DIRECT,
        )
    )
    # A declined request that was edited is back in DRAFT but stays DECLINED.
    if (
        request.status
        not in {Request.RequestStatus.PUBLISHED, Request.RequestStatus.DRAFT}
        or request.direct_status != Request.DirectRequestStatus.DECLINED
    ):
        msg = "Only a declined direct request can be redirected or made public."
        raise InvalidTransitionError(msg)
    return request


@transaction.atomic
def reassign_direct_request(
    request_id: int,
    customer,
    provider: ProviderProfile,
) -> Request:
    request = _get_declined_direct_request_for_update(request_id, customer)
    if (
        provider.pk == request.selected_provider_id
        or request.declined_providers.filter(pk=provider.pk).exists()
    ):
        msg = "This provider has already declined the request."
        raise InvalidTransitionError(msg)

    request.selected_provider = provider
    request.direct_status = Request.DirectRequestStatus.PENDING
    _validate_direct_request_target(request)
    # The content may have been edited since it was last moderated.
    _apply_moderation(request)
    request.save(
        update_fields=["selected_provider", "direct_status", *_MODERATION_FIELDS],
    )
    if request.status == Request.RequestStatus.PUBLISHED:
        _notify_direct_request_submitted(request)
    return request


@transaction.atomic
def convert_direct_request_to_public(request_id: int, customer) -> Request:
    request = _get_declined_direct_request_for_update(request_id, customer)
    request.visibility = Request.RequestVisibility.PUBLIC
    request.selected_provider = None
    request.direct_status = None
    # Public requests reach every provider, so re-check the content.
    _apply_moderation(request)
    request.save(
        update_fields=[
            "visibility",
            "selected_provider",
            "direct_status",
            *_MODERATION_FIELDS,
        ],
    )
    return request


@transaction.atomic
def close_request(request_id: int) -> Request:
    request = Request.objects.select_for_update().get(pk=request_id)
    if request.status != Request.RequestStatus.PUBLISHED:
        msg = "Only a published request can be closed."
        raise InvalidTransitionError(msg)
    request.status = Request.RequestStatus.CLOSED
    request.closed_at = timezone.now()
    request.save(update_fields=["status", "closed_at", "updated_at"])
    return request
