from rest_framework.permissions import SAFE_METHODS
from rest_framework.permissions import BasePermission

from sabil_book.users.models import ProviderProfile


class IsPostAuthorOrReadOnly(BasePermission):
    """Anyone may read; only the provider who wrote a post may change it."""

    message = "You can only change your own posts."

    def has_object_permission(self, request, view, obj) -> bool:
        if request.method in SAFE_METHODS:
            return True
        try:
            profile = request.user.provider_profile
        except AttributeError, ProviderProfile.DoesNotExist:
            return False
        return obj.author_id == profile.pk
