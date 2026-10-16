from dataclasses import dataclass

from .events import NotificationCategory
from .events import NotificationEvent


@dataclass(frozen=True, slots=True)
class NotificationDefinition:
    event: NotificationEvent
    category: NotificationCategory

    @property
    def critical(self) -> bool:
        return self.category != NotificationCategory.ACTIVITY

    @property
    def subject_template(self) -> str:
        return f"notifications/email/{self.event.value}_subject.txt"

    @property
    def body_template(self) -> str:
        return f"notifications/email/{self.event.value}_body.txt"


NOTIFICATION_DEFINITIONS = {
    NotificationEvent.NEW_OFFER: NotificationDefinition(
        event=NotificationEvent.NEW_OFFER,
        category=NotificationCategory.ACTIVITY,
    ),
    NotificationEvent.OFFER_SELECTED: NotificationDefinition(
        event=NotificationEvent.OFFER_SELECTED,
        category=NotificationCategory.ACTIVITY,
    ),
    NotificationEvent.PAYMENT_SUCCEEDED: NotificationDefinition(
        event=NotificationEvent.PAYMENT_SUCCEEDED,
        category=NotificationCategory.MONEY,
    ),
    NotificationEvent.RESULT_DELIVERED: NotificationDefinition(
        event=NotificationEvent.RESULT_DELIVERED,
        category=NotificationCategory.DEADLINE,
    ),
    NotificationEvent.ACCEPTANCE_CONFIRMED: NotificationDefinition(
        event=NotificationEvent.ACCEPTANCE_CONFIRMED,
        category=NotificationCategory.MONEY,
    ),
    NotificationEvent.REVISION_REQUESTED: NotificationDefinition(
        event=NotificationEvent.REVISION_REQUESTED,
        category=NotificationCategory.DEADLINE,
    ),
    NotificationEvent.DISPUTE_OPENED: NotificationDefinition(
        event=NotificationEvent.DISPUTE_OPENED,
        category=NotificationCategory.DEADLINE,
    ),
    NotificationEvent.DISPUTE_RESOLVED: NotificationDefinition(
        event=NotificationEvent.DISPUTE_RESOLVED,
        category=NotificationCategory.MONEY,
    ),
    NotificationEvent.PAYOUT_SENT: NotificationDefinition(
        event=NotificationEvent.PAYOUT_SENT,
        category=NotificationCategory.MONEY,
    ),
}


def get_notification_definition(
    event: NotificationEvent | str,
) -> NotificationDefinition:
    return NOTIFICATION_DEFINITIONS[NotificationEvent(event)]
