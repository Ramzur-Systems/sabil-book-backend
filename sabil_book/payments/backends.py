"""Adapters for development payment flows."""

from .services import confirm_payment
from .services import confirm_payout
from .services import confirm_refund


class SimulatedPaymentBackend:
    """Confirm every operation immediately; no external money is transferred."""

    def initiate_payment(self, payment):
        confirm_payment(payment.pk)

    def issue_payout(self, payout):
        confirm_payout(payout.pk)

    def refund_payment(self, payment):
        confirm_refund(payment.pk)
