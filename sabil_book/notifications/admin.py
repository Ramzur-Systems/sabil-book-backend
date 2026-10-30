from django.contrib import admin
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from .models import NotificationDelivery
from .models import NotificationPreference
from .services import NotificationService


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
    actions = ["requeue"]
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

    @admin.action(description=_("Requeue selected deliveries for sending"))
    def requeue(self, request, queryset):
        """Replay deliveries stuck in queued/sending or that failed."""
        requeueable = queryset.exclude(
            status__in=[
                NotificationDelivery.DeliveryStatus.SENT,
                NotificationDelivery.DeliveryStatus.SKIPPED,
            ],
        )
        ids = list(requeueable.values_list("pk", flat=True))
        requeueable.update(status=NotificationDelivery.DeliveryStatus.QUEUED)
        for delivery_id in ids:
            NotificationService.enqueue_email(delivery_id)
        self.message_user(
            request,
            _("Requeued %(count)d deliveries.") % {"count": len(ids)},
            messages.SUCCESS,
        )
