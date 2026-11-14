"""Adapters must deduplicate external operations using Payment/Payout.pk.

Adapters initiate operations; authenticated provider callbacks invoke confirm_*
services. Returning from an adapter never means money has moved successfully.
"""

from celery import shared_task
from django.db import transaction

from sabil_book.offers.models import Order
from sabil_book.offers.order_services import locked_order
from sabil_book.users.models import ProviderProfile

from .models import Payment
from .models import Payout
from .services import PaymentIntegrationUnavailable
from .services import payment_backend


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def initiate_payment(payment_id):
    payment = Payment.objects.get(pk=payment_id)
    if payment.status not in {
        Payment.PaymentStatus.PENDING,
        Payment.PaymentStatus.PROCESSING,
    }:
        return
    payment_backend(payment.provider).initiate_payment(payment)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def issue_order_payout(order_id):
    with transaction.atomic():
        order = locked_order(order_id)
        if order.status != Order.OrderStatus.COMPLETED:
            return
        payment = order.payments.filter(status=Payment.PaymentStatus.SUCCEEDED).first()
        if payment is None:
            return
        provider = order.offer.provider
        payout = order.payouts.first()
        if payout is None:
            payout = Payout.objects.create(
                order=order,
                amount=payment.amount,
                currency=payment.currency,
                provider=provider.payout_provider or payment.provider,
            )
        if payout.status in {Payout.PayoutStatus.PAID, Payout.PayoutStatus.CANCELLED}:
            return
        if provider.kyc_status != ProviderProfile.KYCStatus.APPROVED:
            payout.status = Payout.PayoutStatus.ON_HOLD
            payout.save(update_fields=["status"])
            return
        if payout.status == Payout.PayoutStatus.ON_HOLD:
            payout.status = Payout.PayoutStatus.PENDING
            payout.save(update_fields=["status"])
    try:
        backend = payment_backend(payout.provider)
    except PaymentIntegrationUnavailable:
        # Keep a durable pending payout until the adapter becomes available.
        return
    backend.issue_payout(payout)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def refund_order(order_id):
    order = Order.objects.get(pk=order_id)
    if order.status != Order.OrderStatus.DISPUTED:
        return
    if not order.dispute.resolution or order.dispute.outcome != "customer":
        return
    payment = order.payments.filter(status=Payment.PaymentStatus.SUCCEEDED).first()
    if payment is None:
        return
    try:
        backend = payment_backend(payment.provider)
    except PaymentIntegrationUnavailable:
        return
    backend.refund_payment(payment)


@shared_task
def recover_order_payments():
    """Recover lost on_commit messages; adapter operations must be idempotent."""
    for payment_id in Payment.objects.filter(status="pending").values_list(
        "pk", flat=True,
    ):
        initiate_payment.delay(payment_id)
    for order_id in Order.objects.filter(status="completed").values_list(
        "pk", flat=True,
    ):
        issue_order_payout.delay(order_id)
    for order_id in (
        Order.objects.filter(
            status="disputed",
            dispute__outcome="customer",
        )
        .exclude(dispute__resolution="")
        .values_list("pk", flat=True)
    ):
        refund_order.delay(order_id)
