from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework.viewsets import ModelViewSet
from rest_framework.viewsets import ReadOnlyModelViewSet

from sabil_book.api import transition_error

from .models import Request
from .serializers import BrowseRequestSerializer
from .serializers import DirectRequestReassignSerializer
from .serializers import RejectionSerializer
from .serializers import RequestSerializer
from .services import InvalidTransitionError
from .services import close_request
from .services import convert_direct_request_to_public
from .services import moderate_request
from .services import reassign_direct_request
from .services import respond_to_direct_request
from .services import submit_request

if TYPE_CHECKING:
    from django.db.models import QuerySet


class RequestViewSet(ModelViewSet):
    serializer_class = RequestSerializer
    queryset = Request.objects.select_related("customer", "selected_provider__user")

    def get_queryset(self) -> QuerySet[Request]:
        user = self.request.user
        return self.queryset.filter(
            Q(customer=user)
            | Q(
                visibility=Request.RequestVisibility.DIRECT,
                selected_provider__user=user,
                status__in=[
                    Request.RequestStatus.PUBLISHED,
                    Request.RequestStatus.CLOSED,
                ],
            ),
        )

    def perform_create(self, serializer: RequestSerializer) -> None:
        serializer.save(
            customer=self.request.user,
            visibility=Request.RequestVisibility.PUBLIC,
            selected_provider=None,
            direct_status=None,
        )

    def _ensure_customer(self, instance: Request) -> None:
        if instance.customer_id != self.request.user.id:
            msg = "Only the request owner may perform this action."
            raise PermissionDenied(msg)

    def perform_update(self, serializer: RequestSerializer) -> None:
        instance = serializer.instance
        self._ensure_customer(instance)
        if instance.is_declined_direct_request:
            # Editing after a decline sends the request back through submission
            # (and moderation) before the provider is asked again.
            serializer.save(
                status=Request.RequestStatus.DRAFT,
                published_at=None,
            )
            return
        serializer.save()

    def update(self, request, *args, **kwargs):
        self._ensure_customer(self.get_object())
        return super().update(request, *args, **kwargs)

    def perform_destroy(self, instance: Request) -> None:
        self._ensure_customer(instance)
        if instance.status != Request.RequestStatus.DRAFT:
            msg = "Only a draft request can be deleted."
            raise InvalidTransitionError(msg)
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except InvalidTransitionError as exc:
            return transition_error(exc)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        instance = self.get_object()
        self._ensure_customer(instance)
        try:
            instance = submit_request(instance.pk)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        instance = self.get_object()
        self._ensure_customer(instance)
        try:
            instance = close_request(instance.pk)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)

    def _respond_to_direct_request(self, request, *, accept: bool):
        try:
            instance = respond_to_direct_request(
                int(self.kwargs["pk"]),
                request.user,
                accept=accept,
            )
        except InvalidTransitionError as exc:
            return transition_error(exc)
        except (Request.DoesNotExist, ValueError) as exc:
            msg = "Request not found."
            raise NotFound(msg) from exc
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._respond_to_direct_request(request, accept=True)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return self._respond_to_direct_request(request, accept=False)

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        instance = self.get_object()
        self._ensure_customer(instance)
        serializer = DirectRequestReassignSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        try:
            instance = reassign_direct_request(
                instance.pk,
                request.user,
                serializer.validated_data["provider"],
            )
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["post"], url_path="make-public")
    def make_public(self, request, pk=None):
        instance = self.get_object()
        self._ensure_customer(instance)
        try:
            instance = convert_direct_request_to_public(instance.pk, request.user)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def categories(self, request):
        data = [
            {"value": value, "label": label}
            for value, label in Request.RequestCategory.choices
        ]
        return Response(data)


class BrowseRequestViewSet(ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = BrowseRequestSerializer
    queryset = Request.objects.filter(
        visibility=Request.RequestVisibility.PUBLIC,
        status__in=[Request.RequestStatus.PUBLISHED, Request.RequestStatus.CLOSED],
    )

    def get_queryset(self) -> QuerySet[Request]:
        queryset = self.queryset.all()
        if category := self.request.query_params.get("category"):
            queryset = queryset.filter(category=category)
        if request_status := self.request.query_params.get("status"):
            queryset = queryset.filter(status=request_status)
        return queryset


class ModerationRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    GenericViewSet,
):
    permission_classes = [IsAdminUser]
    serializer_class = RequestSerializer
    queryset = Request.objects.filter(
        status=Request.RequestStatus.MANUAL_REVIEW,
    ).select_related("customer")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        instance = self.get_object()
        try:
            instance = moderate_request(instance.pk, approve=True)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        instance = self.get_object()
        serializer = RejectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            instance = moderate_request(
                instance.pk,
                approve=False,
                reason=serializer.validated_data["reason"],
            )
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)
