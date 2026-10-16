from rest_framework import serializers

from sabil_book.users.models import ProviderProfile

from .models import Request


class RequestSerializer(serializers.ModelSerializer[Request]):
    class Meta:
        model = Request
        fields = [
            "id",
            "customer",
            "category",
            "title",
            "description",
            "budget",
            "deadline",
            "visibility",
            "selected_provider",
            "direct_status",
            "status",
            "moderation_flags",
            "rejection_reason",
            "created_at",
            "updated_at",
            "published_at",
            "closed_at",
        ]
        read_only_fields = [
            "customer",
            "visibility",
            "selected_provider",
            "direct_status",
            "status",
            "moderation_flags",
            "rejection_reason",
            "created_at",
            "updated_at",
            "published_at",
            "closed_at",
        ]

    def validate(self, attrs):
        instance = self.instance
        if (
            instance
            and instance.status
            not in {
                Request.RequestStatus.DRAFT,
                Request.RequestStatus.REJECTED,
            }
            and not instance.is_declined_direct_request
        ):
            msg = "Only a draft, rejected or declined direct request can be edited."
            raise serializers.ValidationError(msg)
        return attrs


class BrowseRequestSerializer(serializers.ModelSerializer[Request]):
    class Meta:
        model = Request
        fields = [
            "id",
            "category",
            "title",
            "description",
            "budget",
            "deadline",
            "status",
            "published_at",
            "closed_at",
        ]


class RejectionSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class DirectRequestReassignSerializer(serializers.Serializer):
    provider = serializers.PrimaryKeyRelatedField(
        queryset=ProviderProfile.objects.select_related("user"),
    )

    def validate_provider(self, provider: ProviderProfile) -> ProviderProfile:
        user = self.context["request"].user
        if provider.user_id == user.id:
            msg = "You cannot send a direct request to yourself."
            raise serializers.ValidationError(msg)
        if provider.kyc_status != ProviderProfile.KYCStatus.APPROVED:
            msg = "The selected provider must have approved KYC."
            raise serializers.ValidationError(msg)
        return provider
