from rest_framework import serializers

from .models import NotificationPreference


class NotificationPreferenceSerializer(
    serializers.ModelSerializer[NotificationPreference],
):
    class Meta:
        model = NotificationPreference
        fields = ["receive_non_critical", "updated_at"]
        read_only_fields = ["updated_at"]
