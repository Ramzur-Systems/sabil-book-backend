from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    # Orders migrated from legacy statuses (confirmed -> paid_out,
    # reverted -> refunded) have no end timestamp, so attachment retention would
    # never match them. Their real end date is unknown; start the retention
    # period now instead of keeping their attachments forever.
    Order = apps.get_model("offers", "Order")
    orders = Order.objects.using(schema_editor.connection.alias)
    now = timezone.now()
    orders.filter(
        status__in=["completed", "paid_out"],
        completed_at__isnull=True,
    ).update(completed_at=now)
    orders.filter(status="refunded", refunded_at__isnull=True).update(refunded_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("offers", "0013_order_refunded_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
