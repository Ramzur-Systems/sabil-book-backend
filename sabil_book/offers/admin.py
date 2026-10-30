from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from sabil_book.admin_utils import badge

from .models import Attachment
from .models import Offer
from .models import Order


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "request",
        "provider",
        "status_badge",
        "price",
        "delivery_days",
    ]
    list_filter = ["status"]
    list_select_related = ["request", "request__customer", "provider__user"]
    search_fields = [
        "provider__user__email",
        "provider__user__name",
        "request__title",
        "request__customer__email",
    ]
    raw_id_fields = ["request", "provider"]

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj: Offer) -> str:
        colors = {
            Offer.OfferStatus.PENDING: "amber",
            Offer.OfferStatus.ACCEPTED: "green",
            Offer.OfferStatus.REJECTED: "red",
            Offer.OfferStatus.WITHDRAWN: "gray",
            Offer.OfferStatus.EXPIRED: "gray",
        }
        return badge(obj.get_status_display(), colors.get(obj.status, "gray"))


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["offer", "status", "funded_at"]
    list_filter = ["status"]


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ["order"]
    list_filter = ["order__status"]
    search_fields = ["file_hash", "storage_key"]
