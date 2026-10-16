from django.db.models import Q
from rest_framework import serializers

from sabil_book.requests.models import Request

from .models import Message
from .models import Offer


class MessageSerializer(serializers.ModelSerializer[Message]):
    class Meta:
        model = Message
        fields = ["id", "offer", "request", "sender", "body", "date_time"]
        read_only_fields = fields


class OfferRequestQuerySerializer(serializers.Serializer):
    request = serializers.IntegerField(min_value=1)


class OfferSerializer(serializers.ModelSerializer[Offer]):
    request = serializers.PrimaryKeyRelatedField(queryset=Request.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        http_request = self.context.get("request")
        user = getattr(http_request, "user", None)
        if user is not None and user.is_authenticated:
            self.fields["request"].queryset = Request.objects.filter(
                Q(visibility=Request.RequestVisibility.PUBLIC)
                | Q(
                    visibility=Request.RequestVisibility.DIRECT,
                    selected_provider__user=user,
                ),
            )

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
