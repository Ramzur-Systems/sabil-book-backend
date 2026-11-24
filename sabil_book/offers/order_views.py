from django.conf import settings
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from sabil_book.api import transition_error
from sabil_book.exceptions import InvalidTransitionError
from sabil_book.payments.services import initiate_order_payment

from .models import Order
from .order_serializers import CorrectionSerializer
from .order_serializers import DisputeSerializer
from .order_serializers import OrderPaymentSerializer
from .order_serializers import OrderSerializer
from .order_serializers import ResultUploadSerializer
from .order_services import accept_result
from .order_services import open_dispute
from .order_services import request_correction
from .order_services import upload_result


class OrderViewSet(ReadOnlyModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    queryset = Order.objects.select_related(
        "offer__request",
        "offer__provider",
    ).prefetch_related("attachments")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        return self.queryset.filter(
            Q(offer__request__customer=self.request.user)
            | Q(offer__provider__user=self.request.user),
        )

    def _transition(self, service, *args):
        order = self.get_object()
        try:
            result = service(order.pk, self.request.user, *args)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(self.get_serializer(result).data)

    @extend_schema(
        request=None,
        responses={202: OrderPaymentSerializer},
        exclude=not settings.PAYMENTS_ENABLED,
    )
    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        if not settings.PAYMENTS_ENABLED:
            raise NotFound
        order = self.get_object()
        try:
            payment = initiate_order_payment(order.pk, request.user)
        except InvalidTransitionError as exc:
            return transition_error(exc)
        return Response(
            OrderPaymentSerializer(payment).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(request=ResultUploadSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def attachments(self, request, pk=None):
        self.get_object()
        serializer = ResultUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._transition(upload_result, serializer.validated_data["file"])

    @extend_schema(request=None, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._transition(accept_result)

    @extend_schema(request=CorrectionSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"], url_path="request-correction")
    def request_correction(self, request, pk=None):
        self.get_object()
        serializer = CorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._transition(request_correction, serializer.validated_data["reason"])

    @extend_schema(request=DisputeSerializer, responses=OrderSerializer)
    @action(detail=True, methods=["post"])
    def dispute(self, request, pk=None):
        self.get_object()
        serializer = DisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._transition(open_dispute, serializer.validated_data["reason"])
