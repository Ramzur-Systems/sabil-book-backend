from django import template
from django.db.models import Count
from django.db.models import Q

from sabil_book.offers.models import Offer
from sabil_book.requests.models import Request
from sabil_book.users.models import User

register = template.Library()


@register.simple_tag(takes_context=True)
def admin_stats(context) -> dict[str, int | None]:
    """Dashboard counts; a count is None when the viewer can't see that model."""
    user = context["request"].user
    stats: dict[str, int | None] = {
        "awaiting_review": None,
        "requests": None,
        "offers": None,
        "users": None,
    }
    if user.has_perm("requests.view_request") or user.has_perm(
        "requests.change_request",
    ):
        counts = Request.objects.aggregate(
            total=Count("pk"),
            awaiting=Count(
                "pk",
                filter=Q(status=Request.RequestStatus.MANUAL_REVIEW),
            ),
        )
        stats["requests"] = counts["total"]
        stats["awaiting_review"] = counts["awaiting"]
    if user.has_perm("offers.view_offer") or user.has_perm("offers.change_offer"):
        stats["offers"] = Offer.objects.count()
    if user.has_perm("users.view_user") or user.has_perm("users.change_user"):
        stats["users"] = User.objects.count()
    return stats
