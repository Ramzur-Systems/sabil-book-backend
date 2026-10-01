# Create your models here.
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import CharField
from django.db.models import DateTimeField
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from sabil_book.users.models import ProviderProfile


class Offer(models.Model):
    class OfferStatus(models.TextChoices):
        PENDING = "pending", _("Pending")  # submitted, awaiting customer decision
        ACCEPTED = "accepted", _("Accepted")  # customer picked this one
        REJECTED = "rejected", _("Rejected")  # customer declined it
        WITHDRAWN = "withdrawn", _("Withdrawn")  # provider pulled it back
        EXPIRED = "expired", _("Expired")  # after validity window

    request = models.ForeignKey(
        "requests.Request",
        on_delete=models.CASCADE,
        related_name="offers",
        verbose_name=_("request"),
    )
    provider = models.ForeignKey(
        ProviderProfile,
        on_delete=models.CASCADE,
        related_name="offers",
        verbose_name=_("provider"),
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name=_("Offer Price"),
    )
    delivery_days = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name=_("Delivery days"),
    )
    comment = models.TextField(
        blank=True,
        verbose_name=_("Offer Comment"),
    )
    status = CharField(
        _("Offer status"),
        max_length=25,
        choices=OfferStatus.choices,
        default=OfferStatus.PENDING,
    )

    class Meta:
        constraints = [
            # A provider may only have one active (pending/accepted) offer per
            # request. To change an offer, withdraw it first and submit a new one.
            models.UniqueConstraint(
                fields=["request", "provider"],
                condition=Q(status__in=["pending", "accepted"]),
                name="unique_active_offer_per_provider_per_request",
            ),
            models.UniqueConstraint(
                fields=["request"],
                condition=Q(status__in=["accepted"]),
                name="unique_accepted_offer_per_request",
            ),
            models.CheckConstraint(
                condition=Q(price__gt=0),
                name="offer_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(delivery_days__gt=0),
                name="offer_delivery_days_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} on {self.request} ({self.get_status_display()})"


class Message(models.Model):
    offer = models.ForeignKey(
        Offer,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name=_("offer"),
    )

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages",
        verbose_name=_("sender"),
    )
    body = models.CharField(
        _("Message Body"),
        max_length=255,
    )
    date_time = DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.sender}: {self.body[:30]}"


class Order(models.Model):
    class OrderStatus(models.TextChoices):
        FUNDED = "funded", _("Funded")
        SENT = "sent", _("Sent")  # provider marked the work delivered
        # reached either from SENT with no dispute, or from DISPUTE resolved
        # in the provider's favor (payout issued)
        CONFIRMED = "confirmed", _("Confirmed")
        APPEAL = "appeal", _("Appeal")  # unrelated to the dispute flow below
        DISPUTE = "dispute", _("Dispute")  # a dispute is in progress
        UNRESOLVED = "unresolved", _("Unresolved")  # dispute ended with no resolution
        # dispute resolved in the customer's favor, payment returned
        REVERTED = "reverted", _("Reverted")

    offer = models.OneToOneField(
        Offer,
        on_delete=models.CASCADE,
        related_name="order",
        verbose_name=_("offer"),
    )
    status = models.CharField(
        _("status"),
        max_length=25,
        choices=OrderStatus.choices,
        default=OrderStatus.FUNDED,
    )
    funded_at = DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Order #{self.pk} ({self.get_status_display()})"


class Attachment(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("order"),
    )
    file_hash = CharField(
        _("Attached file hash"),
        max_length=255,
        blank=True,
    )
    storage_key = CharField(
        _("Attached file key"),
        max_length=255,
        blank=True,
    )

    def __str__(self):
        return f"Attached file for {self.order} - {self.storage_key}"
