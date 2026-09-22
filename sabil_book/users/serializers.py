from django.contrib.auth.password_validation import validate_password
from django.db.models import Avg
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from sabil_book.reviews.models import Review
from sabil_book.users.models import ProviderProfile
from sabil_book.users.models import User


class UserSerializer(serializers.ModelSerializer[User]):
    """Mirrors the frontend's `User` entity (camelCase, opaque string id)."""

    id = serializers.SerializerMethodField()
    fullName = serializers.CharField(source="name", required=False, allow_blank=True)  # noqa: N815
    isProvider = serializers.SerializerMethodField()  # noqa: N815
    isAdmin = serializers.BooleanField(source="is_staff", read_only=True)  # noqa: N815
    preferredLanguage = serializers.CharField(  # noqa: N815
        source="preferred_language",
        required=False,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "fullName",
            "isProvider",
            "isAdmin",
            "country",
            "preferredLanguage",
        ]
        read_only_fields = ["id", "email", "isProvider", "isAdmin"]

    def get_id(self, obj: User) -> str:
        return str(obj.pk)

    def get_isProvider(self, obj: User) -> bool:  # noqa: N802
        return ProviderProfile.objects.filter(user=obj).exists()


class RegisterSerializer(serializers.ModelSerializer[User]):
    password = serializers.CharField(write_only=True)
    fullName = serializers.CharField(source="name", required=False, allow_blank=True)  # noqa: N815
    isProvider = serializers.BooleanField(  # noqa: N815
        write_only=True,
        required=False,
        default=False,
    )

    class Meta:
        model = User
        fields = ["email", "password", "fullName", "country", "isProvider"]

    def validate_password(self, value: str) -> str:
        dummy_user = User(
            email=self.initial_data.get("email", ""),
            name=self.initial_data.get("fullName", ""),
        )
        validate_password(value, dummy_user)
        return value

    def create(self, validated_data: dict) -> User:
        validated_data.pop("isProvider", None)
        return User.objects.create_user(**validated_data)


class SabilTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Same token pair as the stock view, plus the signed-in user."""

    def validate(self, attrs: dict) -> dict:
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user, context=self.context).data
        return data


def _provider_rating(provider: ProviderProfile) -> float | None:
    average = Review.objects.filter(order__offer__provider=provider).aggregate(
        avg=Avg("rating"),
    )["avg"]
    return float(average) if average is not None else None


class _ProviderRatingMixin:
    rating = serializers.SerializerMethodField()

    def get_rating(self, obj: ProviderProfile) -> float | None:
        return _provider_rating(obj)


class ProviderProfilePublicSerializer(
    _ProviderRatingMixin,
    serializers.ModelSerializer[ProviderProfile],
):
    """Public view of a provider: no payout details."""

    class Meta:
        model = ProviderProfile
        fields = ["id", "country", "kyc_status", "rating"]
        read_only_fields = fields


class ProviderProfileSerializer(
    _ProviderRatingMixin,
    serializers.ModelSerializer[ProviderProfile],
):
    """Full view of a provider profile, for its owner only."""

    class Meta:
        model = ProviderProfile
        fields = ["id", "country", "payout_provider", "kyc_status", "rating"]
        read_only_fields = ["kyc_status"]


class KYCWebhookSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            ProviderProfile.KYCStatus.APPROVED,
            ProviderProfile.KYCStatus.REJECTED,
        ],
    )
    provider_profile_id = serializers.IntegerField()
