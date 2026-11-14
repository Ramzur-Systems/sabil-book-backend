from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Attachment
from .models import Order


class DownloadSerializer(serializers.Serializer):
    url = serializers.URLField()
    expires_in = serializers.IntegerField()


class AttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=DownloadSerializer)
    def get(self, request, pk):
        attachment = get_object_or_404(
            Attachment.objects.select_related("order").filter(
                order__offer__request__customer=request.user,
            ),
            pk=pk,
        )
        available_statuses = {
            Order.OrderStatus.DELIVERED,
            Order.OrderStatus.UNDER_REVIEW,
            Order.OrderStatus.CORRECTION_REQUESTED,
            Order.OrderStatus.DISPUTED,
            Order.OrderStatus.COMPLETED,
            Order.OrderStatus.PAID_OUT,
            Order.OrderStatus.REFUNDED,
        }
        if (
            attachment.av_scan_status != Attachment.ScanStatus.CLEAN
            or attachment.order.status not in available_statuses
            or not attachment.file
        ):
            msg = "This result is not available for download."
            raise PermissionDenied(msg)
        ttl = settings.ATTACHMENT_DOWNLOAD_TTL_SECONDS
        url = attachment.file.storage.url(
            attachment.file.name,
            expire=ttl,
            parameters={"ResponseContentDisposition": 'attachment; filename="result"'},
        )
        return Response(
            {"url": url, "expires_in": ttl},
            headers={"Cache-Control": "private, no-store"},
        )
