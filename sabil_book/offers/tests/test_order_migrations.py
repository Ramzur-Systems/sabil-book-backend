from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from sabil_book.offers.tests.factories import OfferFactory


@pytest.mark.django_db(transaction=True)
def test_legacy_order_statuses_and_review_deadlines_are_migrated(settings):
    settings.ORDER_REVIEW_PERIOD_SECONDS = 2
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    old_target = [("offers", "0009_direct_request_message_provider")]
    statuses = [
        "funded",
        "sent",
        "revision_requested",
        "confirmed",
        "appeal",
        "dispute",
        "unresolved",
        "reverted",
    ]
    offers = [OfferFactory() for _ in statuses]
    try:
        executor.migrate(old_target)
        apps = executor.loader.project_state(old_target).apps
        old_order = apps.get_model("offers", "Order")
        for offer, status in zip(offers, statuses, strict=True):
            old_order.objects.create(offer_id=offer.pk, status=status)
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
        apps = executor.loader.project_state(leaves).apps
        order_model = apps.get_model("offers", "Order")
        expected = [
            "funded",
            "delivered",
            "correction_requested",
            "paid_out",
            "disputed",
            "disputed",
            "disputed",
            "refunded",
        ]
        for offer, status in zip(offers, expected, strict=True):
            order = order_model.objects.get(offer_id=offer.pk)
            assert order.status == status
            if status == "delivered":
                assert order.review_deadline == order.delivered_at + timedelta(
                    seconds=2,
                )
            if status == "funded":
                assert order.funded_at is not None
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
