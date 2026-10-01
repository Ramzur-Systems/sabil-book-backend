from rest_framework import serializers

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
