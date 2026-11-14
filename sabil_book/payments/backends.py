"""Adapters for development payment flows."""

from .services import confirm_payment


class SimulatedPaymentBackend:
    """Confirm collection immediately; no external money is transferred."""

    def initiate_payment(self, payment):
        confirm_payment(payment.pk)
