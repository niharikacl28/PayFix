"""Unit tests for deterministic recovery strategy eligibility."""

import unittest
from decimal import Decimal

from app.eligibility import StrategyEligibility, evaluate_strategies
from app.models import Payment


def payment(**overrides: object) -> Payment:
    base: dict[str, object] = {
        "customer_id": "customer_1", "merchant_id": "merchant_1", "amount": Decimal("8000"),
        "payment_status": "failed", "payment_method": "upi", "available_payment_methods": ["upi"],
        "failure_category": "temporary_network", "is_retryable": True, "risk_level": "low",
    }
    base.update(overrides)
    return Payment(**base)  # type: ignore[arg-type]


def results_for(item: Payment) -> dict[str, StrategyEligibility]:
    return {result.strategy: result for result in evaluate_strategies(item)}


class EligibilityEngineTests(unittest.TestCase):
    def test_temporary_failure_allows_both_retry_strategies(self) -> None:
        results = results_for(payment())
        self.assertTrue(results["retry_now"].eligible)
        self.assertTrue(results["retry_later"].eligible)

    def test_permanent_failure_blocks_retry(self) -> None:
        results = results_for(payment(failure_category="permanent_decline", is_retryable=False))
        self.assertFalse(results["retry_now"].eligible)
        self.assertIn("permanent", results["retry_now"].reason.lower())
        self.assertFalse(results["retry_later"].eligible)

    def test_retry_limit_blocks_further_retry(self) -> None:
        results = results_for(payment(retry_count=2))
        self.assertFalse(results["retry_now"].eligible)
        self.assertIn("limit", results["retry_later"].reason.lower())

    def test_suspected_fraud_blocks_automatic_retry(self) -> None:
        results = results_for(payment(failure_category="suspected_fraud", risk_level="high"))
        self.assertFalse(results["retry_now"].eligible)
        self.assertIn("high-risk", results["retry_now"].reason.lower())

    def test_upi_only_customer_has_no_alternate_method(self) -> None:
        result = results_for(payment())["alternate_payment_method"]
        self.assertFalse(result.eligible)
        self.assertEqual(result.metadata["available_alternative_methods"], [])

    def test_customer_with_upi_and_debit_card_can_use_alternate_method(self) -> None:
        result = results_for(payment(available_payment_methods=["upi", "debit_card"]))["alternate_payment_method"]
        self.assertTrue(result.eligible)
        self.assertEqual(result.metadata["available_alternative_methods"], ["debit_card"])

    def test_contact_limit_blocks_customer_facing_actions(self) -> None:
        results = results_for(payment(customer_contact_count=2))
        self.assertFalse(results["customer_reminder"].eligible)
        self.assertFalse(results["payment_link"].eligible)
        self.assertIn("contact limit", results["payment_link"].reason.lower())

    def test_high_value_payment_exposes_automated_recovery_restriction(self) -> None:
        result = results_for(payment(amount=Decimal("20000")))["retry_now"]
        self.assertFalse(result.eligible)
        self.assertTrue(result.metadata["automated_amount_exceeds_limit"])
        self.assertIn("amount exceeds", result.reason.lower())

    def test_high_value_payment_blocks_customer_facing_automation(self) -> None:
        results = results_for(payment(amount=Decimal("20000"), available_payment_methods=["upi", "debit_card"]))
        self.assertFalse(results["payment_link"].eligible)
        self.assertFalse(results["customer_reminder"].eligible)
        self.assertFalse(results["alternate_payment_method"].eligible)

    def test_expired_card_and_expired_payment_method_block_retry(self) -> None:
        for category in ("expired_card", "expired_payment_method"):
            results = results_for(payment(failure_category=category, is_retryable=False))
            self.assertFalse(results["retry_now"].eligible)
            self.assertFalse(results["retry_later"].eligible)

    def test_human_escalation_handles_high_risk_high_value_and_repeated_failures(self) -> None:
        self.assertTrue(results_for(payment(risk_level="high"))["human_escalation"].eligible)
        self.assertTrue(results_for(payment(amount=Decimal("20000")))["human_escalation"].eligible)
        self.assertTrue(results_for(payment(retry_count=2))["human_escalation"].eligible)

    def test_stop_is_always_present(self) -> None:
        results = results_for(payment(payment_status="succeeded", is_retryable=False))
        self.assertTrue(results["stop"].eligible)

    def test_every_ineligible_strategy_has_a_reason(self) -> None:
        for result in evaluate_strategies(payment(failure_category="permanent_decline", is_retryable=False, customer_contact_count=2)):
            if not result.eligible:
                self.assertTrue(result.reason.strip())


if __name__ == "__main__":
    unittest.main()
