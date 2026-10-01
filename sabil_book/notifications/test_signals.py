import pytest
from django.core import mail

from sabil_book.offers.models import Offer
from sabil_book.offers.models import Order
from sabil_book.offers.tests.factories import OfferFactory
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.payments.models import Payment
from sabil_book.payments.models import Payout
from sabil_book.payments.tests.factories import PaymentFactory
from sabil_book.payments.tests.factories import PayoutFactory
from sabil_book.reviews.tests.factories import DisputeFactory


@pytest.mark.django_db(transaction=True)
def test_domain_lifecycle_triggers_all_notification_emails():
    offer = OfferFactory.create(status=Offer.OfferStatus.PENDING)
    expected_subjects = {f"New offer for “{offer.request.title}”"}

    offer.status = Offer.OfferStatus.ACCEPTED
    offer.save(update_fields=["status"])
    expected_subjects.add("Your offer was selected")

    order = OrderFactory.create(offer=offer, status=Order.OrderStatus.FUNDED)
    payment = PaymentFactory.create(
        order=order,
        status=Payment.PaymentStatus.PENDING,
    )
    payment.status = Payment.PaymentStatus.SUCCEEDED
    payment.save(update_fields=["status"])
    expected_subjects.add(f"Payment succeeded for order #{order.pk}")

    order.status = Order.OrderStatus.SENT
    order.save(update_fields=["status"])
    expected_subjects.add(f"Result delivered for order #{order.pk}")

    order.status = Order.OrderStatus.CONFIRMED
    order.save(update_fields=["status"])
    expected_subjects.add(f"Result accepted for order #{order.pk}")

    order.status = Order.OrderStatus.REVISION_REQUESTED
    order.save(update_fields=["status"])
    expected_subjects.add(f"Revision requested for order #{order.pk}")

    dispute = DisputeFactory.create(order=order, resolution="")
    expected_subjects.add(f"Dispute opened for order #{order.pk}")
    dispute.resolution = "The customer receives a partial refund."
    dispute.save(update_fields=["resolution"])
    expected_subjects.add(f"Dispute resolved for order #{order.pk}")

    payout = PayoutFactory.create(order=order, status=Payout.PayoutStatus.PENDING)
    payout.status = Payout.PayoutStatus.PAID
    payout.save(update_fields=["status"])
    expected_subjects.add(f"Payout sent for order #{order.pk}")

    assert expected_subjects == {message.subject for message in mail.outbox}


@pytest.mark.django_db(transaction=True)
def test_saving_an_unrelated_field_does_not_repeat_a_status_notification():
    offer = OfferFactory.create(status=Offer.OfferStatus.PENDING)
    mail.outbox.clear()

    offer.price = 125
    offer.save(update_fields=["price"])

    assert mail.outbox == []
