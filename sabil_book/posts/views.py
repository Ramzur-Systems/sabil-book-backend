from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import AnonRateThrottle
from rest_framework.viewsets import ModelViewSet

from sabil_book.users.models import ProviderProfile

from .models import Post
from .permissions import IsPostAuthorOrReadOnly
from .serializers import PostSerializer
from .serializers import PostUpdateSerializer

if TYPE_CHECKING:
    from django.db.models import QuerySet


class PostPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class PostAnonRateThrottle(AnonRateThrottle):
    """Limit unauthenticated scraping of the public post feed."""

    scope = "posts_anon"


class PostFilterSerializer(serializers.Serializer):
    provider = serializers.IntegerField(required=False, min_value=1)


class PostViewSet(ModelViewSet):
    """Public posts, with creation and changes limited to their provider author."""

    serializer_class = PostSerializer
    pagination_class = PostPagination
    queryset = Post.objects.prefetch_related("images")
    throttle_classes = [PostAnonRateThrottle]

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [AllowAny()]
        return [IsAuthenticated(), IsPostAuthorOrReadOnly()]

    def get_serializer_class(self):
        if self.action in {"update", "partial_update"}:
            return PostUpdateSerializer
        return super().get_serializer_class()

    def get_queryset(self) -> QuerySet[Post]:
        queryset = self.queryset.all()
        if self.action != "list":
            return queryset
        filters = PostFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        if (provider_id := filters.validated_data.get("provider")) is not None:
            queryset = queryset.filter(author_id=provider_id)
        return queryset

    def perform_create(self, serializer: PostSerializer) -> None:
        try:
            author = self.request.user.provider_profile
        except ProviderProfile.DoesNotExist as exc:
            msg = "A provider profile is required."
            raise PermissionDenied(msg) from exc
        serializer.save(author=author)
