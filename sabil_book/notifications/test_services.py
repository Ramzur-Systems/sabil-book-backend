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


@pytest.mark.django_db
def test_text_parts_are_not_html_escaped():
    recipient = UserFactory.create()
    delivery = NotificationDelivery.objects.create(
        recipient=recipient,
        event=NotificationEvent.NEW_OFFER,
        context={
            "request_title": "Tom & Jerry's <logo>",
            "provider_name": "P",
            "price": "1",
            "request_id": 1,
        },
    )

    send_notification_email.run(delivery.pk)

    message = cast("EmailMultiAlternatives", mail.outbox[0])
    assert "Tom & Jerry's <logo>" in message.subject
    assert "Tom & Jerry's <logo>" in message.body
    html = str(message.alternatives[0][0])
    assert "Tom &amp; Jerry" in html
    assert "&amp;amp;" not in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        NotificationDelivery.DeliveryStatus.SENDING,
        NotificationDelivery.DeliveryStatus.SENT,
        NotificationDelivery.DeliveryStatus.FAILED,
    ],
)
def test_task_does_not_send_a_delivery_that_is_not_claimable(status):
    delivery = NotificationDelivery.objects.create(
        recipient=UserFactory.create(),
        event=NotificationEvent.PAYOUT_SENT,
        context={"order_id": 1},
        status=status,
    )

    send_notification_email.run(delivery.pk)

    assert mail.outbox == []
    delivery.refresh_from_db()
    assert delivery.status == status
    assert delivery.attempts == 0


@pytest.mark.django_db
def test_permanent_errors_fail_without_retrying():
    delivery = NotificationDelivery.objects.create(
        recipient=UserFactory.create(),
        event=NotificationEvent.PAYOUT_SENT,
        channel="sms",
        context={"order_id": 1},
    )

    with patch.object(send_notification_email, "retry") as retry:
        send_notification_email.run(delivery.pk)

    retry.assert_not_called()
    delivery.refresh_from_db()
    assert delivery.status == NotificationDelivery.DeliveryStatus.FAILED
    assert delivery.attempts == 1


@pytest.mark.django_db
def test_notification_errors_do_not_break_the_business_save():
    from sabil_book.offers.tests.factories import OfferFactory  # noqa: PLC0415

    with patch.object(
        NotificationService,
        "notify",
        side_effect=RuntimeError("boom"),
    ):
        offer = OfferFactory.create()

    assert offer.pk is not None
