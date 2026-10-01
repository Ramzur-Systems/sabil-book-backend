from rest_framework import serializers

from .models import Message


class MessageSerializer(serializers.ModelSerializer[Message]):
    class Meta:
        model = Message
        fields = ["id", "offer", "sender", "body", "date_time"]
        read_only_fields = fields
from .models import Offer


class OfferRequestQuerySerializer(serializers.Serializer):
    request = serializers.IntegerField(min_value=1)


class OfferSerializer(serializers.ModelSerializer[Offer]):
    class Meta:
        model = Offer
        fields = [
            "id",
            "request",
            "provider",
            "price",
            "delivery_days",
            "comment",
            "status",
        ]
        read_only_fields = [
            "provider",
            "status",
        ]
