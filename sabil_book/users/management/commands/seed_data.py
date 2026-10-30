from __future__ import annotations

import itertools
import os
import random
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from sabil_book.notifications.signals import notifications_disabled
from sabil_book.offers.models import Order
from sabil_book.offers.services import accept_offer
from sabil_book.requests.models import Request

DISPUTE_SAMPLE_RATE = 3
DISPUTED_ORDER_STATUSES = (
    Order.OrderStatus.DISPUTE,
    Order.OrderStatus.UNRESOLVED,
    Order.OrderStatus.REVERTED,
)


ADMIN_EMAIL = "admin@example.com"

# Cycled over to give the admin a realistic moderation queue (mostly pending).
MODERATION_STATUSES = (
    Request.RequestStatus.MANUAL_REVIEW,
    Request.RequestStatus.MANUAL_REVIEW,
    Request.RequestStatus.MANUAL_REVIEW,
    Request.RequestStatus.REJECTED,
    Request.RequestStatus.DRAFT,
)


def _seed_moderation_requests(count, customers, request_factory):
    blocklist = list(settings.REQUEST_MODERATION_BLOCKLIST) or ["flagged"]
    created = []
    for index in range(count):
        status = MODERATION_STATUSES[index % len(MODERATION_STATUSES)]
        flagged = status in {
            Request.RequestStatus.MANUAL_REVIEW,
            Request.RequestStatus.REJECTED,
        }
        created.append(
            request_factory.create(
                customer=customers[index % len(customers)],
                status=status,
                moderation_flags=[blocklist[index % len(blocklist)]] if flagged else [],
                rejection_reason=(
                    "Violates our content policy."
                    if status == Request.RequestStatus.REJECTED
                    else ""
                ),
            ),
        )
    return created


def _ensure_admin() -> str | None:
    """Create the admin if missing; return the password only when created."""
    user_model = get_user_model()
    if user_model.objects.filter(email=ADMIN_EMAIL).exists():
        return None
    password = os.environ.get("SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    user_model.objects.create_superuser(
        email=ADMIN_EMAIL,
        password=password,
        name="Admin",
    )
    return password


def _validate_counts(n_customers, n_providers, n_requests, n_offers, n_messages):
    if n_requests > 0 and n_customers + n_providers == 0:
        msg = "Need at least one customer or provider to create requests."
        raise CommandError(msg)
    if n_offers > 0 and n_requests == 0:
        msg = "Need at least one request to create offers."
        raise CommandError(msg)
    if n_messages > 0 and n_offers == 0:
        msg = "Need at least one offer to create messages."
        raise CommandError(msg)


def _seed_post_order_data(
    orders,
    review_factory,
    payment_factory,
    dispute_factory,
    payout_factory,
):
    confirmed_orders = [
        order for order in orders if order.status == Order.OrderStatus.CONFIRMED
    ]
    for order in orders:
        payment_factory.create(order=order)
    for order in confirmed_orders:
        review_factory.create(order=order)

    disputed_orders = [
        order for order in orders if order.status in DISPUTED_ORDER_STATUSES
    ]
    for index, order in enumerate(disputed_orders):
        if index % DISPUTE_SAMPLE_RATE == 0:
            dispute_factory.create(order=order)

    payouts = [payout_factory.create(order=order) for order in confirmed_orders]
    return confirmed_orders, payouts


class Command(BaseCommand):
    help = (
        "Seed the local database with fake users, provider profiles, requests, "
        "offers, messages, orders, reviews, disputes, payments, and payouts."
    )

    def add_arguments(self, parser):
        parser.add_argument("--customers", type=int, default=20)
        parser.add_argument("--providers", type=int, default=5)
        parser.add_argument("--requests", type=int, default=15)
        parser.add_argument("--offers", type=int, default=20)
        parser.add_argument("--messages", type=int, default=40)
        parser.add_argument("--moderation-requests", type=int, default=10)

    def handle(self, *args, **options):
        if not settings.DEBUG:
            msg = "Refusing to seed data outside of DEBUG environments."
            raise CommandError(msg)

        # Imported here, not at module scope: these pull in factory-boy, a
        # dev-only dependency that isn't installed in the production image.
        from sabil_book.offers.tests.factories import MessageFactory  # noqa: PLC0415
        from sabil_book.offers.tests.factories import OfferFactory  # noqa: PLC0415
        from sabil_book.offers.tests.factories import OrderFactory  # noqa: PLC0415
        from sabil_book.payments.tests.factories import PaymentFactory  # noqa: PLC0415
        from sabil_book.payments.tests.factories import PayoutFactory  # noqa: PLC0415
        from sabil_book.requests.tests.factories import RequestFactory  # noqa: PLC0415
        from sabil_book.reviews.tests.factories import DisputeFactory  # noqa: PLC0415
        from sabil_book.reviews.tests.factories import ReviewFactory  # noqa: PLC0415
        from sabil_book.users.tests.factories import (  # noqa: PLC0415
            ProviderProfileFactory,
        )
        from sabil_book.users.tests.factories import UserFactory  # noqa: PLC0415

        n_customers = options["customers"]
        n_providers = options["providers"]
        n_requests = options["requests"]
        n_offers = options["offers"]
        n_messages = options["messages"]
        _validate_counts(n_customers, n_providers, n_requests, n_offers, n_messages)

        with transaction.atomic(), notifications_disabled():
            customers = UserFactory.create_batch(n_customers)
            provider_users = UserFactory.create_batch(n_providers)
            providers = [
                ProviderProfileFactory.create(user=provider_user)
                for provider_user in provider_users
            ]

            requesters = customers + provider_users
            requests = [
                RequestFactory.create(customer=requesters[index % len(requesters)])
                for index in range(n_requests)
            ]

            # Offer has a unique constraint on (request, provider) while
            # pending/accepted, so each offer needs a distinct pair to avoid
            # intermittent seed failures.
            request_provider_pairs = list(itertools.product(requests, providers))
            random.shuffle(request_provider_pairs)
            if n_offers > len(request_provider_pairs):
                msg = (
                    f"Cannot create {n_offers} offers from only "
                    f"{len(request_provider_pairs)} distinct request/provider pairs."
                )
                raise CommandError(msg)
            offers = [
                OfferFactory.create(request=request, provider=provider)
                for request, provider in request_provider_pairs[:n_offers]
            ]

            accepted_offers = []
            accepted_request_ids = set()
            for offer in offers:
                if offer.request_id in accepted_request_ids:
                    continue
                service_request = offer.request
                service_request.status = Request.RequestStatus.PUBLISHED
                service_request.published_at = timezone.now()
                service_request.save(
                    update_fields=["status", "published_at", "updated_at"],
                )
                accepted_offers.append(
                    accept_offer(offer.pk, service_request.customer),
                )
                accepted_request_ids.add(offer.request_id)

            order_statuses = Order.OrderStatus.values
            orders = [
                OrderFactory.create(
                    offer=offer,
                    status=order_statuses[index % len(order_statuses)],
                )
                for index, offer in enumerate(accepted_offers)
            ]

            for index in range(n_messages):
                MessageFactory.create(
                    offer=offers[index % len(offers)],
                    sender=requesters[index % len(requesters)],
                )

            confirmed_orders, payouts = _seed_post_order_data(
                orders,
                ReviewFactory,
                PaymentFactory,
                DisputeFactory,
                PayoutFactory,
            )

            if options["moderation_requests"] and requesters:
                _seed_moderation_requests(
                    options["moderation_requests"],
                    requesters,
                    RequestFactory,
                )

            admin_password = _ensure_admin()

        self._report(
            options,
            n_orders=len(orders),
            n_reviews=len(confirmed_orders),
            n_payouts=len(payouts),
            admin_password=admin_password,
        )

    def _report(self, options, *, n_orders, n_reviews, n_payouts, admin_password):
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {options['customers']} customers, "
                f"{options['providers']} providers, "
                f"{options['requests']} requests, {options['offers']} offers, "
                f"{options['messages']} messages, "
                f"{n_orders} orders (from accepted offers), a payment per order, "
                f"{n_reviews} reviews and {n_payouts} payouts for "
                f"confirmed orders, and disputes on every {DISPUTE_SAMPLE_RATE}rd "
                f"order in a dispute-related state, and "
                f"{options['moderation_requests']} moderation-queue requests.",
            ),
        )
        if admin_password:
            self.stdout.write(f"Admin login: {ADMIN_EMAIL} / {admin_password}")
        else:
            self.stdout.write(f"Admin {ADMIN_EMAIL} already exists; unchanged.")
