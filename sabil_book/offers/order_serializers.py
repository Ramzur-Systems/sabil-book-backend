from django.conf import settings
from rest_framework import serializers

from sabil_book.payments.models import Payment

from .antivirus import validate_result_file
from .models import Attachment
from .models import Order


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "file_hash", "av_scan_status", "av_scanned_at", "created_at"]
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    attachments = AttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "offer",
            "status",
            "funded_at",
            "delivered_at",
            "review_deadline",
            "completed_at",
            "correction_reason",
            "attachments",
            "delivery_due_date",
        ]
        read_only_fields = fields

    def get_fields(self):
        fields = super().get_fields()
        if not settings.PAYMENTS_ENABLED:
            fields.pop("funded_at")  # Payment timing is hidden for now.
        return fields


class ResultUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        return validate_result_file(value)


class CorrectionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000, allow_blank=False)


class DisputeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, allow_blank=False)


class OrderPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "status", "provider", "currency", "amount"]
        read_only_fields = fields
