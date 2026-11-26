from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    # Orders funded before delivery deadlines existed have no due date, so the
    # overdue dispute path would never open for them. Funded orders get their
    # real due date. A correction's request time was never recorded, so
    # correction_requested orders get a fresh window from now, which is the
    # rule new corrections follow.
    Order = apps.get_model("offers", "Order")
    orders = Order.objects.using(schema_editor.connection.alias)
    now = timezone.now()
    pending = orders.filter(
        status__in=["funded", "correction_requested"],
        delivery_due_date__isnull=True,
    ).select_related("offer")
    updated = []
    for order in pending.iterator(chunk_size=500):
        days = timedelta(days=order.offer.delivery_days)
        if order.status == "funded" and order.funded_at:
            order.delivery_due_date = order.funded_at + days
        else:
            order.delivery_due_date = now + days
        updated.append(order)
    orders.bulk_update(updated, ["delivery_due_date"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("offers", "0015_order_delivery_due_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
