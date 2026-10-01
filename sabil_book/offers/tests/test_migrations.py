from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.tests.factories import ProviderProfileFactory

MIGRATE_FROM = [("offers", "0005_move_request_to_requests_app")]
MIGRATE_TO = [
    (
        "offers",
        "0006_offer_comment_offer_delivery_days_alter_offer_price_and_more",
    ),
]


@pytest.mark.django_db(transaction=True)
def test_offer_migration_normalizes_legacy_rows():
    service_request = RequestFactory()
    first = OfferFactory(
        request=service_request,
        provider=ProviderProfileFactory(),
    )
    second = OfferFactory(
        request=service_request,
        provider=ProviderProfileFactory(),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_FROM)
    old_apps = executor.loader.project_state(MIGRATE_FROM).apps
    old_offer = old_apps.get_model("offers", "Offer")
    old_offer.objects.filter(pk__in=[first.pk, second.pk]).update(
        price=0,
        status="accepted",
    )

    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_TO)
    new_apps = executor.loader.project_state(MIGRATE_TO).apps
    migrated_offer = new_apps.get_model("offers", "Offer")
    migrated = migrated_offer.objects.filter(pk__in=[first.pk, second.pk])

    assert set(migrated.values_list("price", flat=True)) == {Decimal("0.01")}
    assert migrated.filter(status="accepted").count() == 1
    assert migrated.filter(status="rejected").count() == 1
