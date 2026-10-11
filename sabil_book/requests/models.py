from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Request(models.Model):
    class RequestCategory(models.TextChoices):
        RESEARCH_BRIEF = "research_brief", _("Research brief")
        PROFESSIONAL_TEMPLATE = (
            "professional_template",
            _(
                "Professional template / SOP",
            ),
        )
        CERTIFICATION_MATERIAL = (
            "certification_material",
            _(
                "Certification material",
            ),
        )

    class RequestStatus(models.TextChoices):
        DRAFT = "draft", _("Draft")
        MANUAL_REVIEW = "manual_review", _("Manual review")
        PUBLISHED = "published", _("Published")
        REJECTED = "rejected", _("Rejected")
        CLOSED = "closed", _("Closed")

    class RequestVisibility(models.TextChoices):
        PUBLIC = "public", _("Public")
        DIRECT = "direct", _("Direct")

    class DirectRequestStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="material_requests",
        verbose_name=_("user"),
    )
    category = models.CharField(
        _("Request Category"),
        max_length=32,
        choices=RequestCategory.choices,
    )
    title = models.CharField(_("title"), max_length=255)
    description = models.TextField(_("description"))
    budget = models.DecimalField(
        _("Request Budget"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    deadline = models.DateField(_("deadline"), null=True, blank=True)
    visibility = models.CharField(
        _("request visibility"),
        max_length=16,
        choices=RequestVisibility.choices,
        default=RequestVisibility.PUBLIC,
        db_index=True,
    )
    selected_provider = models.ForeignKey(
        "users.ProviderProfile",
        on_delete=models.PROTECT,
        related_name="direct_requests",
        verbose_name=_("selected provider"),
        null=True,
        blank=True,
    )
    direct_status = models.CharField(  # noqa: DJ001
        _("direct request status"),
        max_length=16,
        choices=DirectRequestStatus.choices,
        null=True,
        blank=True,
    )
    status = models.CharField(
        _("Request Status"),
        max_length=32,
        choices=RequestStatus.choices,
        default=RequestStatus.DRAFT,
    )
    moderation_flags = models.JSONField(_("moderation flags"), default=list, blank=True)
    rejection_reason = models.TextField(_("rejection reason"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)
    closed_at = models.DateTimeField(_("closed at"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(budget__gte=0) | models.Q(budget__isnull=True),
                name="request_budget_non_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        visibility="public",
                        selected_provider__isnull=True,
                        direct_status__isnull=True,
                    )
                    | models.Q(
                        visibility="direct",
                        selected_provider__isnull=False,
                        direct_status__isnull=False,
                    )
                ),
                name="request_direct_fields_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.customer} - {self.get_category_display()}"

    @property
    def is_declined_direct_request(self) -> bool:
        return (
            self.visibility == self.RequestVisibility.DIRECT
            and self.status == self.RequestStatus.PUBLISHED
            and self.direct_status == self.DirectRequestStatus.DECLINED
        )
