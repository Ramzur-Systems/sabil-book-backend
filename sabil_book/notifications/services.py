from __future__ import annotations

import json
import logging
from functools import partial
from typing import TYPE_CHECKING
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from .events import NotificationChannel
from .events import NotificationEvent
from .models import NotificationDelivery
from .models import NotificationPreference
from .registry import get_notification_definition

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


class NotificationService:
    """Channel-agnostic entry point used by the business modules."""

    @classmethod
    def notify(
        cls,
        *,
        event: NotificationEvent | str,
        recipient,
        context: Mapping[str, Any] | None = None,
    ) -> NotificationDelivery:
        event = NotificationEvent(event)
        definition = get_notification_definition(event)
        recipient_id = getattr(recipient, "pk", recipient)
        if recipient_id is None:
            msg = "A saved user or user ID is required as the notification recipient."
            raise ValueError(msg)

        serialized_context = json.loads(
            json.dumps(dict(context or {}), cls=DjangoJSONEncoder),
        )
        preference = (
            NotificationPreference.objects.filter(user_id=recipient_id)
            .values_list("receive_non_critical", flat=True)
            .first()
        )
        should_skip = preference is False and not definition.critical
        delivery = NotificationDelivery.objects.create(
            recipient_id=recipient_id,
            event=event,
            channel=NotificationChannel.EMAIL,
            status=(
                NotificationDelivery.DeliveryStatus.SKIPPED
                if should_skip
                else NotificationDelivery.DeliveryStatus.QUEUED
            ),
            context=serialized_context,
        )
        if not should_skip:
            transaction.on_commit(partial(cls.enqueue_email, delivery.pk))
        return delivery

    @classmethod
    def notify_many(
        cls,
        *,
        event: NotificationEvent | str,
        recipients: Iterable,
        context: Mapping[str, Any] | None = None,
    ) -> list[NotificationDelivery]:
        deliveries = []
        seen_recipient_ids = set()
        for recipient in recipients:
            recipient_id = getattr(recipient, "pk", recipient)
            if recipient_id in seen_recipient_ids:
                continue
            seen_recipient_ids.add(recipient_id)
            deliveries.append(
                cls.notify(event=event, recipient=recipient, context=context),
            )
        return deliveries

    @staticmethod
    def enqueue_email(delivery_id: int) -> None:
        from .tasks import send_notification_email  # noqa: PLC0415

        try:
            send_notification_email.delay(delivery_id)
        except Exception:
            # A broker outage must not turn a successful business transaction into
            # a failed request. The queued delivery remains visible for replay.
            logger.exception(
                "Could not enqueue email notification delivery %s.",
                delivery_id,
            )
