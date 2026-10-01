from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from .events import NotificationChannel
from .events import NotificationEvent


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
        verbose_name=_("user"),
    )
    receive_non_critical = models.BooleanField(
        _("receive non-critical notifications"),
        default=True,
        help_text=_(
            "Disable activity notifications while keeping money and deadline events.",
        ),
    )
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    def __str__(self) -> str:
        return f"Notification settings for {self.user}"


class NotificationDelivery(models.Model):
    class DeliveryStatus(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        RETRYING = "retrying", _("Retrying")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_deliveries",
        verbose_name=_("recipient"),
    )
    event = models.CharField(
        _("event"),
        max_length=32,
        choices=[
            (event.value, event.name.replace("_", " ").title())
            for event in NotificationEvent
        ],
    )
    channel = models.CharField(
        _("channel"),
        max_length=16,
        choices=[
            (channel.value, channel.name.title()) for channel in NotificationChannel
        ],
        default=NotificationChannel.EMAIL,
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.QUEUED,
    )
    context = models.JSONField(_("template context"), default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    last_error = models.TextField(_("last error"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event} via {self.channel} to {self.recipient} ({self.status})"
