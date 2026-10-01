from django.contrib import admin

from .models import NotificationDelivery
from .models import NotificationPreference


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["user", "receive_non_critical", "updated_at"]
    list_filter = ["receive_non_critical"]
    search_fields = ["user__email"]


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ["recipient", "event", "channel", "status", "attempts", "created_at"]
    list_filter = ["event", "channel", "status"]
    search_fields = ["recipient__email", "last_error"]
    readonly_fields = [
        "recipient",
        "event",
        "channel",
        "status",
        "context",
        "attempts",
        "last_error",
        "created_at",
        "sent_at",
    ]
