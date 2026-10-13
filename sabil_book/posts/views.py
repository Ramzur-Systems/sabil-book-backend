from __future__ import annotations

from typing import TYPE_CHECKING
from typing import cast

from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from sabil_book.users.models import ProviderProfile

from .models import Post
from .serializers import PostSerializer

if TYPE_CHECKING:
    from django.db.models import QuerySet


class PostViewSet(ModelViewSet):
    """Public posts, with creation and changes limited to their provider author."""

    serializer_class = PostSerializer
    queryset = Post.objects.select_related("author").prefetch_related("images")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self) -> QuerySet[Post]:
        queryset = self.queryset.all()
        if self.action != "list":
            return queryset
        if provider_id := self.request.query_params.get("provider"):
            if not provider_id.isdigit():
                msg = "provider must be a numeric id."
                raise ValidationError({"provider": msg})
            queryset = queryset.filter(author_id=provider_id)
        return queryset

    def _provider_profile(self) -> ProviderProfile:
        try:
            return self.request.user.provider_profile
        except ProviderProfile.DoesNotExist as exc:
            msg = "A provider profile is required."
            raise PermissionDenied(msg) from exc

    def perform_create(self, serializer: PostSerializer) -> None:
        serializer.save(author=self._provider_profile())

    def perform_update(self, serializer: PostSerializer) -> None:
        post = cast("Post", serializer.instance)
        if post.author_id != self._provider_profile().pk:
            msg = "You can only edit your own posts."
            raise PermissionDenied(msg)
        serializer.save()

    def perform_destroy(self, instance: Post) -> None:
        if instance.author_id != self._provider_profile().pk:
            msg = "You can only delete your own posts."
            raise PermissionDenied(msg)
        instance.delete()
