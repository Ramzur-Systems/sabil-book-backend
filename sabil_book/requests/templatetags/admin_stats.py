from django import template

from sabil_book.offers.models import Offer
from sabil_book.requests.models import Request
from sabil_book.users.models import User

register = template.Library()


@register.simple_tag
def admin_stats() -> dict[str, int]:
    return {
        "awaiting_review": Request.objects.filter(
            status=Request.RequestStatus.MANUAL_REVIEW,
        ).count(),
        "requests": Request.objects.count(),
        "offers": Offer.objects.count(),
        "users": User.objects.count(),
    }
