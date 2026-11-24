from django.conf import settings
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import models

# Create your models here.
from django.db.models import CharField
from django.db.models import DecimalField
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from sabil_book.offers.models import Order


class Review(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("order"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("author"),
    )
    # headroom kept for a possible future 0-10 scale; enforced range is 1-5 today
    rating = DecimalField(
        _("Review Rating"),
        max_digits=3,
        decimal_places=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    body = CharField(
        _("Review Body"),
        max_length=255,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "author"],
                name="unique_review_per_order_author",
            ),
            models.CheckConstraint(
                condition=Q(rating__gte=1) & Q(rating__lte=5),
                name="review_rating_range",
            ),
        ]

    def __str__(self) -> str:
        return f"Review of {self.order} ({self.rating})"


class Dispute(models.Model):
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="dispute",
        verbose_name=_("order"),
    )
    reason = CharField(
        _("Dispute reason"),
        max_length=255,
        default="",
    )
    outcome = CharField(
        max_length=16,
        blank=True,
        choices=[("provider", "Provider"), ("customer", "Customer")],
    )
    resolution = CharField(
        _("Dispute resolution"),
        max_length=255,
        default="",
    )

    def __str__(self) -> str:
        return f"Dispute on {self.order} ({self.reason})"
