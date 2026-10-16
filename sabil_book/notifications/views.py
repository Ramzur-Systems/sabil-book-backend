from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import NotificationPreference
from .serializers import NotificationPreferenceSerializer


class NotificationPreferenceViewSet(GenericViewSet):
    serializer_class = NotificationPreferenceSerializer

    @action(detail=False, methods=["get", "patch"])
    def me(self, request):
        preference, _ = NotificationPreference.objects.get_or_create(
            user=request.user,
        )
        if request.method == "PATCH":
            serializer = self.get_serializer(
                preference,
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
        else:
            serializer = self.get_serializer(preference)
        return Response(serializer.data)
