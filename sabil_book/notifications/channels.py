from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .events import NotificationChannel

if TYPE_CHECKING:
    from .models import NotificationDelivery
    from .registry import NotificationDefinition


class NotificationDeliveryError(RuntimeError):
    """Raised when a channel could not deliver a notification."""


class UnsupportedNotificationChannelError(NotImplementedError):
    """Raised for channel adapters that are intentionally only scaffolds."""


class NotificationChannelBackend(Protocol):
    @staticmethod
    def send(
        delivery: NotificationDelivery,
        definition: NotificationDefinition,
    ) -> None: ...


class EmailNotificationChannel:
    @staticmethod
    def send(
        delivery: NotificationDelivery,
        definition: NotificationDefinition,
    ) -> None:
        recipient = delivery.recipient
        context = {
            **delivery.context,
            "recipient": recipient,
            "recipient_name": recipient.name or recipient.email,
            "event": definition.event.value,
        }
        subject = " ".join(
            render_to_string(definition.subject_template, context).splitlines(),
        ).strip()
        body = render_to_string(definition.body_template, context).strip()
        html_body = render_to_string(
            "notifications/email/base.html",
            {**context, "message": body},
        )
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient.email],
        )
        message.attach_alternative(html_body, "text/html")
        if message.send() != 1:
            msg = f"Email backend did not accept notification delivery {delivery.pk}."
            raise NotificationDeliveryError(msg)


class SmsNotificationChannel:
    """Extension point for a future SMS provider adapter."""

    @staticmethod
    def send(
        delivery: NotificationDelivery,
        definition: NotificationDefinition,
    ) -> None:
        del delivery, definition
        msg = "The SMS notification channel is not configured yet."
        raise UnsupportedNotificationChannelError(msg)


class PushNotificationChannel:
    """Extension point for a future push provider adapter."""

    @staticmethod
    def send(
        delivery: NotificationDelivery,
        definition: NotificationDefinition,
    ) -> None:
        del delivery, definition
        msg = "The push notification channel is not configured yet."
        raise UnsupportedNotificationChannelError(msg)


CHANNEL_BACKENDS: dict[
    NotificationChannel,
    type[NotificationChannelBackend],
] = {
    NotificationChannel.EMAIL: EmailNotificationChannel,
    NotificationChannel.SMS: SmsNotificationChannel,
    NotificationChannel.PUSH: PushNotificationChannel,
}
