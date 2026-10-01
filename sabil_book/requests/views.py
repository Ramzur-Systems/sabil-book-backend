from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework.viewsets import ModelViewSet
from rest_framework.viewsets import ReadOnlyModelViewSet

from sabil_book.api import transition_error

from .models import Request
from .serializers import BrowseRequestSerializer
from .serializers import RejectionSerializer
from .serializers import RequestSerializer
from .services import InvalidTransitionError
from .services import close_request
from .services import moderate_request
from .services import submit_request

if TYPE_CHECKING:
    from django.db.models import QuerySet


class RequestViewSet(ModelViewSet):
    serializer_class = RequestSerializer
    queryset = Request.objects.select_related("customer")

    def get_queryset(self) -> QuerySet[Request]:
        return self.queryset.filter(customer=self.request.user)

    def perform_create(self, serializer: RequestSerializer) -> None:
        serializer.save(customer=self.request.user)

    def perform_destroy(self, instance: Request) -> None:
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
        try:
            instance = submit_request(instance.pk)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        instance = self.get_object()
        try:
            instance = close_request(instance.pk)
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
