import uuid
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CharField
from django.db.models import EmailField
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractUser):
    """
    Default custom user model for sabil_book.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    # First and last name do not cover name patterns around the globe
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = EmailField(_("email address"), unique=True)
    username = None  # type: ignore[assignment]
    country = CharField(_("User location"), blank=True, max_length=255)
    preferred_language = CharField(
        _("Preferred language"),
        blank=True,
        max_length=10,
        default="en",
    )
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()


class ProviderProfile(models.Model):
    class KYCStatus(models.TextChoices):
        NOT_SUBMITTED = "not_submitted", _("Not Submitted")
        PENDING = "pending", _("Pending Review")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provider_profile",
        verbose_name=_("user"),
    )
    country = CharField(_("Provider country"), blank=True, max_length=255)
    kyc_status = models.CharField(
        _("KYC status"),
        max_length=20,
        choices=KYCStatus.choices,
        default=KYCStatus.NOT_SUBMITTED,
    )
    payout_provider = models.CharField(
        _("Payout provider"),
        max_length=225,
        blank=True,
    )

    def __str__(self) -> str:
        return f"{self.user} ({self.get_kyc_status_display()})"
