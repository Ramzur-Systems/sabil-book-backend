import logging

from celery import shared_task
from django.db import models
from django.template import TemplateDoesNotExist
from django.utils import timezone

from .channels import CHANNEL_BACKENDS
from .channels import UnsupportedNotificationChannelError
from .events import NotificationChannel
from .models import NotificationDelivery
from .registry import get_notification_definition

logger = logging.getLogger(__name__)

MAX_EMAIL_RETRIES = 5
CLAIMABLE_STATUSES = [
    NotificationDelivery.DeliveryStatus.QUEUED,
    NotificationDelivery.DeliveryStatus.RETRYING,
]
PERMANENT_ERRORS = (
    UnsupportedNotificationChannelError,
    TemplateDoesNotExist,
    KeyError,
    ValueError,
)


@shared_task(
    bind=True,
    max_retries=MAX_EMAIL_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_notification_email(self, delivery_id: int) -> int | None:
    """Render and send one email, retrying transient delivery failures.

    The delivery is claimed with a conditional UPDATE, so a redelivered or
    duplicated task cannot send the same email twice. A delivery left in
    SENDING by a crashed worker is not re-sent automatically (that could
    duplicate the email); requeue it from the admin once checked.
    """
    claimed = NotificationDelivery.objects.filter(
        pk=delivery_id,
        status__in=CLAIMABLE_STATUSES,
    ).update(
        status=NotificationDelivery.DeliveryStatus.SENDING,
        attempts=models.F("attempts") + 1,
        last_error="",
    )
    if not claimed:
        return (
            NotificationDelivery.objects.filter(pk=delivery_id)
            .values_list(
                "pk",
                flat=True,
            )
            .first()
        )

    delivery = NotificationDelivery.objects.select_related("recipient").get(
        pk=delivery_id,
    )
    try:
        definition = get_notification_definition(delivery.event)
        backend = CHANNEL_BACKENDS[NotificationChannel(delivery.channel)]
        backend.send(delivery, definition)
    except PERMANENT_ERRORS as exc:
        # Retrying cannot fix a missing template or an unconfigured channel.
        NotificationDelivery.objects.filter(pk=delivery_id).update(
            status=NotificationDelivery.DeliveryStatus.FAILED,
            last_error=str(exc)[:2000],
        )
        logger.exception("Notification delivery %s failed permanently.", delivery_id)
        return None
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
