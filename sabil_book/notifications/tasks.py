from celery import shared_task
from django.db import models
from django.db import transaction
from django.utils import timezone

from .channels import CHANNEL_BACKENDS
from .events import NotificationChannel
from .models import NotificationDelivery
from .registry import get_notification_definition

MAX_EMAIL_RETRIES = 5


@shared_task(
    bind=True,
    max_retries=MAX_EMAIL_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_notification_email(self, delivery_id: int) -> int | None:
    """Render and send one email, retrying transient delivery failures."""
    with transaction.atomic():
        try:
            delivery = (
                NotificationDelivery.objects.select_for_update()
                .select_related("recipient")
                .get(pk=delivery_id)
            )
        except NotificationDelivery.DoesNotExist:
            return None

        if delivery.status in {
            NotificationDelivery.DeliveryStatus.SENT,
            NotificationDelivery.DeliveryStatus.SKIPPED,
        }:
            return delivery.pk
        delivery.status = NotificationDelivery.DeliveryStatus.SENDING
        delivery.attempts = models.F("attempts") + 1
        delivery.last_error = ""
        delivery.save(update_fields=["status", "attempts", "last_error"])

    try:
        definition = get_notification_definition(delivery.event)
        backend = CHANNEL_BACKENDS[NotificationChannel(delivery.channel)]
        backend.send(delivery, definition)
    except Exception as exc:
        is_final_attempt = self.request.retries >= MAX_EMAIL_RETRIES
        NotificationDelivery.objects.filter(pk=delivery_id).update(
            status=(
                NotificationDelivery.DeliveryStatus.FAILED
                if is_final_attempt
                else NotificationDelivery.DeliveryStatus.RETRYING
            ),
            last_error=str(exc)[:2000],
        )
        if is_final_attempt:
            raise
        countdown = min(60 * (2**self.request.retries), 60 * 60)
        raise self.retry(exc=exc, countdown=countdown) from exc

    NotificationDelivery.objects.filter(pk=delivery_id).update(
        status=NotificationDelivery.DeliveryStatus.SENT,
        sent_at=timezone.now(),
        last_error="",
    )
    return delivery_id
