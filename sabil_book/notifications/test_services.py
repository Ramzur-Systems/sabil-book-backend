from typing import TYPE_CHECKING
from typing import cast
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.core import mail

from sabil_book.users.tests.factories import UserFactory

from .channels import EmailNotificationChannel
from .events import NotificationEvent
from .models import NotificationDelivery
from .models import NotificationPreference
from .services import NotificationService
from .tasks import send_notification_email

if TYPE_CHECKING:
    from django.core.mail import EmailMultiAlternatives


@pytest.mark.parametrize("event", list(NotificationEvent))
@pytest.mark.django_db(transaction=True)
def test_every_event_renders_and_sends_an_email(event):
    recipient = UserFactory.create()

    delivery = NotificationService.notify(
        event=event,
        recipient=recipient,
        context={
            "request_title": "Market research",
            "request_id": 10,
            "offer_id": 20,
            "provider_name": "Provider",
            "price": "100.00",
            "order_id": 30,
            "payment_id": 40,
            "payout_id": 50,
            "dispute_id": 60,
            "amount": "100.00",
            "currency": "USD",
            "reason": "The result is incomplete.",
            "resolution": "A refund was approved.",
        },
    )

    delivery.refresh_from_db()
    assert delivery.status == NotificationDelivery.DeliveryStatus.SENT
    assert delivery.attempts == 1
    assert len(mail.outbox) == 1
    message = cast("EmailMultiAlternatives", mail.outbox[0])
    assert message.to == [recipient.email]
    assert getattr(message.alternatives[0], "mimetype", None) == "text/html"


@pytest.mark.django_db(transaction=True)
def test_non_critical_preference_skips_activity_but_keeps_money_events():
    recipient = UserFactory.create()
    NotificationPreference.objects.create(
        user=recipient,
        receive_non_critical=False,
    )

    activity_delivery = NotificationService.notify(
        event=NotificationEvent.NEW_OFFER,
        recipient=recipient,
    )
    money_delivery = NotificationService.notify(
        event=NotificationEvent.PAYMENT_SUCCEEDED,
        recipient=recipient,
        context={"order_id": 1},
    )

    activity_delivery.refresh_from_db()
    money_delivery.refresh_from_db()
    assert activity_delivery.status == NotificationDelivery.DeliveryStatus.SKIPPED
    assert money_delivery.status == NotificationDelivery.DeliveryStatus.SENT
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_email_failure_is_marked_for_retry():
    recipient = UserFactory.create()
    delivery = NotificationDelivery.objects.create(
        recipient=recipient,
        event=NotificationEvent.PAYOUT_SENT,
        context={"order_id": 1},
    )

    with (
        patch.object(
            EmailNotificationChannel,
            "send",
            side_effect=OSError("SMTP unavailable"),
        ),
        patch.object(send_notification_email, "retry", side_effect=Retry()) as retry,
        pytest.raises(Retry),
    ):
        send_notification_email.run(delivery.pk)

    delivery.refresh_from_db()
    assert delivery.status == NotificationDelivery.DeliveryStatus.RETRYING
    assert delivery.attempts == 1
    assert delivery.last_error == "SMTP unavailable"
    retry.assert_called_once()


def test_broker_failure_does_not_escape_to_business_code():
    with patch(
        "sabil_book.notifications.tasks.send_notification_email.delay",
        side_effect=OSError("Redis unavailable"),
    ):
        NotificationService.enqueue_email(123)
