"""Payment adapter boundary, with an explicit development simulation mode."""

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.offers.models import Order
from sabil_book.offers.order_services import locked_order
from sabil_book.offers.order_services import require_customer
from sabil_book.transactions import on_commit_robust

from .models import Payment
from .models import Payout


class PaymentIntegrationUnavailable(APIException):
    status_code = 503
    default_detail = "Payment provider is not configured for this country."


SIMULATED_PROVIDER = "simulated"


def payment_backend(provider):
    if provider == SIMULATED_PROVIDER and settings.PAYMENT_SIMULATION_ENABLED:
        from .backends import SimulatedPaymentBackend  # noqa: PLC0415

        return SimulatedPaymentBackend()
    path = settings.PAYMENT_BACKENDS.get(provider)
    if not path:
        raise PaymentIntegrationUnavailable
    return import_string(path)()


@transaction.atomic
def initiate_order_payment(order_id, user):
    from .tasks import initiate_payment  # noqa: PLC0415

    order = locked_order(order_id)
    require_customer(order, user)
    if order.status != Order.OrderStatus.AWAITING_PAYMENT:
        msg = "Only an unpaid order can be paid."
        raise InvalidTransitionError(msg)
    existing = order.payments.filter(status__in=["pending", "processing"]).first()
    if existing:
        return existing
    simulation = settings.PAYMENT_SIMULATION_ENABLED
    config = settings.PAYMENT_PROVIDERS_BY_COUNTRY.get(user.country.upper())
    if simulation:
        currency = (config or {}).get("currency")
        if currency not in Payment.PaymentCurrency.values:
            currency = {"KZ": "KZT", "QA": "QAR"}.get(user.country.upper(), "USD")
        config = {"provider": "simulated", "currency": currency}
    else:
        if not config or config.get("currency") not in Payment.PaymentCurrency.values:
            raise PaymentIntegrationUnavailable
        payment_backend(config["provider"])
    payment = Payment.objects.create(
        order=order,
        amount=order.offer.price,
        provider=config["provider"],
        currency=config["currency"],
    )
    if simulation:
        from .backends import SimulatedPaymentBackend  # noqa: PLC0415

        SimulatedPaymentBackend().initiate_payment(payment)
        payment.refresh_from_db()
    else:
        on_commit_robust(initiate_payment.delay, payment.pk)
    return payment


@transaction.atomic
def confirm_payment(payment_id):
    payment = Payment.objects.get(pk=payment_id)
    order = locked_order(payment.order_id)
    payment.refresh_from_db()
    if payment.status == Payment.PaymentStatus.SUCCEEDED:
        return order
    if payment.status not in {
        Payment.PaymentStatus.PENDING,
        Payment.PaymentStatus.PROCESSING,
    }:
        msg = "This payment cannot be confirmed."
        raise InvalidTransitionError(msg)
    order.transition_to(Order.OrderStatus.FUNDED)
    order.funded_at = timezone.now()
    order.save(update_fields=["status", "funded_at"])
    payment.status = Payment.PaymentStatus.SUCCEEDED
    payment.save(update_fields=["status"])
    return order


@transaction.atomic
def confirm_payout(payout_id):
    payout = Payout.objects.get(pk=payout_id)
    order = locked_order(payout.order_id)
    payout.refresh_from_db()
    if payout.status == Payout.PayoutStatus.PAID:
        return order
    if payout.status not in {
        Payout.PayoutStatus.PENDING,
        Payout.PayoutStatus.PROCESSING,
    }:
        msg = "This payout cannot be confirmed."
        raise InvalidTransitionError(msg)
    order.transition_to(Order.OrderStatus.PAID_OUT)
    order.save(update_fields=["status"])
    payout.status = Payout.PayoutStatus.PAID
    payout.save(update_fields=["status"])
    return order


@transaction.atomic
def confirm_refund(payment_id):
    payment = Payment.objects.get(pk=payment_id)
    order = locked_order(payment.order_id)
    payment.refresh_from_db()
    if payment.status == Payment.PaymentStatus.REFUNDED:
        return order
    if payment.status != Payment.PaymentStatus.SUCCEEDED:
        msg = "Only a successful payment can be refunded."
        raise InvalidTransitionError(msg)
    if order.status != Order.OrderStatus.DISPUTED:
        msg = "Only a disputed order can be refunded."
        raise InvalidTransitionError(msg)
    if not order.dispute.resolution or order.dispute.outcome != "customer":
        msg = "A customer-favour dispute resolution is required."
        raise InvalidTransitionError(msg)
    order.transition_to(Order.OrderStatus.REFUNDED)
    order.refunded_at = timezone.now()
    order.save(update_fields=["status", "refunded_at"])
    payment.status = Payment.PaymentStatus.REFUNDED
    payment.save(update_fields=["status"])
    return order
