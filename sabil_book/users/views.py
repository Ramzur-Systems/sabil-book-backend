import hmac
import uuid

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import CreateAPIView
from rest_framework.mixins import CreateModelMixin
from rest_framework.mixins import ListModelMixin
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.mixins import UpdateModelMixin
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from sabil_book.users.models import ProviderProfile
from sabil_book.users.models import User
from sabil_book.users.serializers import KYCWebhookSerializer
from sabil_book.users.serializers import ProviderProfilePublicSerializer
from sabil_book.users.serializers import ProviderProfileSerializer
from sabil_book.users.serializers import RegisterSerializer
from sabil_book.users.serializers import SabilTokenObtainPairSerializer
from sabil_book.users.serializers import UserSerializer


def _serialize_user(request):
    return UserSerializer(request.user, context={"request": request}).data


class UserViewSet(RetrieveModelMixin, ListModelMixin, UpdateModelMixin, GenericViewSet):
    serializer_class = UserSerializer
    queryset = User.objects.all()
    lookup_field = "pk"

    def get_queryset(self, *args, **kwargs):
        assert isinstance(self.request.user.id, int)
        return self.queryset.filter(id=self.request.user.id)

    @action(detail=False, methods=["get", "patch"])
    def me(self, request):
        if request.method == "PATCH":
            serializer = UserSerializer(
                request.user,
                data=request.data,
                partial=True,
                context={"request": request},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(status=status.HTTP_200_OK, data=serializer.data)

        return Response(status=status.HTTP_200_OK, data=_serialize_user(request))


class RegisterView(CreateAPIView):
    """Register a new user and return it together with a JWT token pair."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    queryset = User.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        is_provider = bool(request.data.get("isProvider", False))
        user = serializer.save()
        if is_provider:
            ProviderProfile.objects.get_or_create(
                user=user,
                defaults={"country": user.country},
            )
        refresh = RefreshToken.for_user(user)
        data = {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": UserSerializer(user, context=self.get_serializer_context()).data,
        }
        return Response(data, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """Same token pair as the stock view, plus the signed-in user."""

    serializer_class = SabilTokenObtainPairSerializer


class LogoutView(APIView):
    """Blacklist the given refresh token, effectively logging the user out."""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            raise ValidationError({"refresh": "This field is required."})
        try:
            token = RefreshToken(refresh_token)
            token_user_id = token.get(jwt_settings.USER_ID_CLAIM)
            request_user_id = getattr(request.user, jwt_settings.USER_ID_FIELD)
            if str(token_user_id) != str(request_user_id):
                raise ValidationError({"refresh": "This token does not belong to you."})
            token.blacklist()
        except TokenError as exc:
            raise ValidationError({"refresh": str(exc)}) from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)


class CurrentUserView(APIView):
    """Return or update the currently authenticated user."""

    def get(self, request, *args, **kwargs):
        return Response(status=status.HTTP_200_OK, data=_serialize_user(request))

    def patch(self, request, *args, **kwargs):
        serializer = UserSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK, data=serializer.data)


class ProviderProfileViewSet(CreateModelMixin, RetrieveModelMixin, GenericViewSet):
    """Become a provider, view a provider's public profile, manage your own."""

    queryset = ProviderProfile.objects.all()
    lookup_field = "pk"

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ProviderProfilePublicSerializer
        return ProviderProfileSerializer

    def get_permissions(self):
        if self.action == "retrieve":
            return [AllowAny()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                serializer.save(user=self.request.user)
        except IntegrityError as exc:
            raise ValidationError(
                {"detail": "You already have a provider profile."},
            ) from exc

    def _get_own_profile(self, request):
        return get_object_or_404(ProviderProfile, user=request.user)

    @action(detail=False, methods=["patch"], url_path="me")
    def me(self, request):
        profile = self._get_own_profile(request)
        serializer = self.get_serializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK, data=serializer.data)

    @action(detail=False, methods=["post"], url_path="me/kyc/initiate")
    def initiate_kyc(self, request):
        """Stub: kick off an external KYC/AML check and mark it pending."""
        profile = self._get_own_profile(request)
        profile.kyc_status = ProviderProfile.KYCStatus.PENDING
        profile.save(update_fields=["kyc_status"])
        data = {
            "verification_id": str(uuid.uuid4()),
            "kyc_status": profile.kyc_status,
        }
        return Response(status=status.HTTP_202_ACCEPTED, data=data)


class KYCWebhookView(APIView):
    """Stub callback endpoint for a KYC/AML provider's verification result.

    Not user-authenticated (it's a server-to-server call from the provider);
    guarded instead by a shared secret set via KYC_WEBHOOK_SECRET.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        secret = request.headers.get("X-Webhook-Secret", "")
        if not settings.KYC_WEBHOOK_SECRET or not hmac.compare_digest(
            secret,
            settings.KYC_WEBHOOK_SECRET,
        ):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        serializer = KYCWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = get_object_or_404(
            ProviderProfile,
            pk=serializer.validated_data["provider_profile_id"],
        )
        profile.kyc_status = serializer.validated_data["status"]
        profile.save(update_fields=["kyc_status"])
        return Response(status=status.HTTP_200_OK)
