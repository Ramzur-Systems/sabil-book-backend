from __future__ import annotations

from django.db import IntegrityError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from sabil_book.api import transition_error
from sabil_book.exceptions import InvalidTransitionError
from sabil_book.requests.models import Request
from sabil_book.users.models import ProviderProfile

from .models import Message
from .models import Offer
from .pagination import MessageCursorPagination
from .permissions import is_offer_participant
from .serializers import MessageSerializer
from .serializers import OfferRequestQuerySerializer
from .serializers import OfferSerializer
from .services import accept_offer


class MessageListView(ListAPIView):
    """Paginated chat history for an offer.

    Lets a client that opens the chat mid-conversation (or reconnects after
    a drop) fetch what it missed, instead of relying solely on the
    WebSocket stream. Access is restricted to the same two participants
    allowed into the WebSocket room — see
    `sabil_book.offers.permissions.is_offer_participant`, shared with
    `OfferChatConsumer` so the rule can't drift between the two entry points.
    """

    serializer_class = MessageSerializer
    pagination_class = MessageCursorPagination

    def get_queryset(self):
        offer = get_object_or_404(
            Offer.objects.select_related("request", "provider"),
            pk=self.kwargs["offer_id"],
        )
        if not is_offer_participant(self.request.user, offer):
            # 404, not 403: don't reveal that the offer exists to non-participants.
            raise NotFound
        return Message.objects.filter(offer=offer).select_related("sender")


class OfferViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, GenericViewSet):
    lookup_value_regex = r"\d+"
    serializer_class = OfferSerializer
    permission_classes = [IsAuthenticated]
    queryset = Offer.objects.select_related("request", "provider")

    @action(detail=False, methods=["get"], url_path="for-request")
    def for_request(self, request):
        query_serializer = OfferRequestQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        offers = self.queryset.filter(
            request_id=query_serializer.validated_data["request"],
            request__customer=request.user,
        )
        serializer = self.get_serializer(offers, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        try:
            offer = accept_offer(int(pk), request.user)
        except Request.DoesNotExist as exc:
            msg = "Offer not found."
            raise NotFound(msg) from exc
        except InvalidTransitionError as exc:
            return transition_error(exc)

        return Response(self.get_serializer(offer).data)

    def perform_create(self, serializer: OfferSerializer) -> None:
        provider = ProviderProfile.objects.filter(user=self.request.user).first()
        if provider is None:
            msg = "A provider profile is required."
            raise PermissionDenied(msg)

        if provider.kyc_status != ProviderProfile.KYCStatus.APPROVED:
            msg = "Approved KYC is required."
            raise PermissionDenied(msg)
        offer_request = serializer.validated_data["request"]

        if offer_request.status != Request.RequestStatus.PUBLISHED:
            msg = "A published request is required"
            raise ValidationError(msg)

        duplicate_error = {
            "request": "You already have an active offer for this request.",
        }
        active_statuses = [Offer.OfferStatus.PENDING, Offer.OfferStatus.ACCEPTED]
        if Offer.objects.filter(
            request=offer_request,
            provider=provider,
            status__in=active_statuses,
        ).exists():
            raise ValidationError(duplicate_error)

        try:
            with transaction.atomic():
                serializer.save(provider=provider)
        except IntegrityError as exc:
            cause = exc.__cause__
            constraint_name = getattr(
                getattr(cause, "diag", None),
                "constraint_name",
                None,
            )
            if constraint_name == "unique_active_offer_per_provider_per_request":
                raise ValidationError(duplicate_error) from exc
            raise

    def get_queryset(self):
        return self.queryset.filter(provider__user=self.request.user)
