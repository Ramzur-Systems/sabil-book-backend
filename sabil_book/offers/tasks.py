from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import Order
from .order_services import process_review_timer


@shared_task
def process_order_reviews():
    """Periodic DB-backed timer; safe to repeat after worker/broker downtime.

    No long-lived ETA messages: corrections clear the deadline and redelivery
    replaces it, so an old timer can never complete a new review round.
    """
    ids = Order.objects.filter(
        Q(status=Order.OrderStatus.DELIVERED)
        | Q(status=Order.OrderStatus.UNDER_REVIEW, review_deadline__lte=timezone.now()),
    ).values_list("pk", flat=True)
    completed = 0
    for order_id in ids.iterator():
        try:
            completed += process_review_timer(order_id)
        except Order.DoesNotExist:
            continue
    return completed
