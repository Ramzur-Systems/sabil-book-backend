from enum import StrEnum


class NotificationEvent(StrEnum):
    NEW_OFFER = "new_offer"
    OFFER_SELECTED = "offer_selected"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    RESULT_DELIVERED = "result_delivered"
    ACCEPTANCE_CONFIRMED = "acceptance_confirmed"
    REVISION_REQUESTED = "revision_requested"
    DISPUTE_OPENED = "dispute_opened"
    DISPUTE_RESOLVED = "dispute_resolved"
    PAYOUT_SENT = "payout_sent"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class NotificationCategory(StrEnum):
    """Preference groups used to keep money/deadline notifications enabled."""

    ACTIVITY = "activity"
    DEADLINE = "deadline"
    MONEY = "money"
