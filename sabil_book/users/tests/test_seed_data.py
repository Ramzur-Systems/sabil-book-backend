import pytest
from django.core.management import call_command

from sabil_book.offers.models import Offer
from sabil_book.offers.models import Order
from sabil_book.payments.models import Payment
from sabil_book.payments.models import Payout
from sabil_book.requests.models import Request
from sabil_book.reviews.models import Dispute
from sabil_book.reviews.models import Review
from sabil_book.users.models import User

SEED_COUNT = 7


@pytest.mark.django_db
def test_seed_data_creates_consistent_post_offer_data(settings):
    settings.DEBUG = True

    call_command(
        "seed_data",
        customers=1,
        providers=1,
        requests=SEED_COUNT,
        offers=SEED_COUNT,
        messages=0,
        verbosity=0,
    )

    assert Offer.objects.filter(status=Offer.OfferStatus.ACCEPTED).count() == SEED_COUNT
    assert Order.objects.count() == SEED_COUNT
    assert Payment.objects.count() == SEED_COUNT
    assert Review.objects.exists()
    assert Dispute.objects.exists()
    assert Payout.objects.exists()


@pytest.mark.django_db
def test_seed_data_creates_moderation_queue_and_admin(settings):

    settings.DEBUG = True

    call_command(
        "seed_data",
        customers=2,
        providers=1,
        requests=1,
        offers=1,
        messages=0,
        moderation_requests=5,
        verbosity=0,
    )

    assert Request.objects.filter(
        status=Request.RequestStatus.MANUAL_REVIEW,
        moderation_flags__isnull=False,
    ).exists()
    assert User.objects.get(email="admin@example.com").is_superuser
