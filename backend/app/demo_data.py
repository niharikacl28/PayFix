"""Synthetic failed-payment records for local development and future recovery tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from .models import Payment
from .repositories import PayFixRepository


DEMO_PAYMENTS = [
    Payment("cust_1001", "merchant_demo", Decimal("1299.00"), "failed", "card", ["card", "upi"], "pay_demo_network", failure_reason="Issuer network timeout", failure_category="temporary_network", is_retryable=True, risk_level="low", successful_payment_count=14, failed_payment_count=1, customer_lifetime_value=Decimal("18600"), retry_count=0),
    Payment("cust_1002", "merchant_demo", Decimal("4999.00"), "failed", "card", ["card", "netbanking"], "pay_demo_funds", failure_reason="Insufficient funds", failure_category="insufficient_funds", is_retryable=True, risk_level="low", successful_payment_count=5, failed_payment_count=2, customer_lifetime_value=Decimal("9400"), retry_count=1, customer_contact_count=1),
    Payment("cust_1003", "merchant_demo", Decimal("799.00"), "failed", "card", ["upi"], "pay_demo_expired", failure_reason="Card expired", failure_category="expired_card", is_retryable=False, risk_level="medium", successful_payment_count=9, failed_payment_count=1, customer_lifetime_value=Decimal("7200")),
    Payment("cust_1004", "merchant_demo", Decimal("24999.00"), "failed", "netbanking", ["netbanking"], "pay_demo_decline", failure_reason="Issuer permanently declined payment", failure_category="permanent_decline", is_retryable=False, risk_level="medium", failed_payment_count=4, retry_count=2, customer_contact_count=2),
    Payment("cust_1005", "merchant_demo", Decimal("15450.00"), "failed", "card", ["card"], "pay_demo_fraud", failure_reason="Transaction flagged for suspected fraud", failure_category="suspected_fraud", is_retryable=False, risk_level="high", successful_payment_count=22, failed_payment_count=1, customer_lifetime_value=Decimal("84500")),
    Payment("cust_1006", "merchant_demo", Decimal("299.00"), "failed", "upi", ["upi"], "pay_demo_subscription", failure_reason="Recurring UPI mandate unavailable", failure_category="recurring_mandate_failure", is_retryable=True, risk_level="low", successful_payment_count=18, failed_payment_count=3, customer_lifetime_value=Decimal("5382"), retry_count=1, customer_contact_count=1),
]


def load_demo_data(database_path: str | Path | None = None) -> int:
    """Insert demo payments once, returning the number of records newly added."""
    repository = PayFixRepository(database_path)
    inserted = 0
    for payment in DEMO_PAYMENTS:
        if repository.get_payment(payment.payment_id) is None:
            repository.create_payment(payment)
            inserted += 1
    return inserted
