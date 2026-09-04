"""Tests for deterministic synthetic strategy simulation and ranking."""

import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.diagnosis_providers import DeterministicDiagnosisProvider
from app.diagnosis_service import DiagnosisService
from app.models import Payment
from app.optimizer import ExpectedRecoveryOptimizer
from app.repositories import PayFixRepository


class StrategyOptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-optimizer-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        diagnosis_service = DiagnosisService(self.repository, DeterministicDiagnosisProvider())
        self.optimizer = ExpectedRecoveryOptimizer(self.repository, diagnosis_service)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def optimize(self, **overrides: object):
        values: dict[str, object] = {
            "payment_id": f"pay_{uuid4().hex}", "customer_id": "customer_1", "merchant_id": "merchant_1",
            "amount": Decimal("8000"), "payment_status": "failed", "payment_method": "upi",
            "available_payment_methods": ["upi"], "failure_category": "temporary_network",
            "failure_reason": "Issuer timeout", "is_retryable": True, "risk_level": "low",
            "successful_payment_count": 3, "failed_payment_count": 0, "retry_count": 0, "customer_contact_count": 0,
        }
        values.update(overrides)
        payment = Payment(**values)  # type: ignore[arg-type]
        self.repository.create_payment(payment)
        return self.optimizer.optimize_payment(payment.payment_id)

    @staticmethod
    def outcomes(result):
        return {outcome.strategy: outcome for outcome in result.strategies_evaluated}

    def test_temporary_failure_evaluates_both_retry_strategies(self) -> None:
        outcomes = self.outcomes(self.optimize())
        self.assertIn("retry_now", outcomes)
        self.assertIn("retry_later", outcomes)

    def test_delayed_retry_exposes_drop_off_assumption(self) -> None:
        outcome = self.outcomes(self.optimize())["retry_later"]
        self.assertTrue(any("Timing/drop-off" in item for item in outcome.assumptions))

    def test_immediate_retry_is_not_always_selected(self) -> None:
        result = self.optimize(failure_category="insufficient_funds", failure_reason="Insufficient funds")
        self.assertNotEqual(result.selected_strategy, "retry_now")
        self.assertGreater(self.outcomes(result)["retry_later"].success_probability, self.outcomes(result)["retry_now"].success_probability)

    def test_insufficient_funds_favors_delayed_over_immediate_retry(self) -> None:
        outcomes = self.outcomes(self.optimize(failure_category="insufficient_funds", failure_reason="Insufficient funds"))
        self.assertGreater(outcomes["retry_later"].expected_recovered_amount, outcomes["retry_now"].expected_recovered_amount)

    def test_permanent_decline_does_not_simulate_retry(self) -> None:
        outcomes = self.outcomes(self.optimize(failure_category="permanent_decline", is_retryable=False))
        self.assertNotIn("retry_now", outcomes)
        self.assertNotIn("retry_later", outcomes)

    def test_upi_only_customer_does_not_simulate_alternate_card(self) -> None:
        self.assertNotIn("alternate_payment_method", self.outcomes(self.optimize()))

    def test_recorded_debit_card_allows_alternate_method_simulation(self) -> None:
        self.assertIn("alternate_payment_method", self.outcomes(self.optimize(available_payment_methods=["upi", "debit_card"])))

    def test_suspected_fraud_remains_restricted(self) -> None:
        outcomes = self.outcomes(self.optimize(failure_category="suspected_fraud", risk_level="high", is_retryable=False))
        self.assertEqual(set(outcomes), {"human_escalation", "stop"})

    def test_high_value_payment_respects_existing_restrictions(self) -> None:
        outcomes = self.outcomes(self.optimize(amount=Decimal("20000")))
        self.assertNotIn("retry_now", outcomes)
        self.assertIn("human_escalation", outcomes)

    def test_stop_is_always_simulated(self) -> None:
        self.assertIn("stop", self.outcomes(self.optimize()))

    def test_optimizer_ranks_by_expected_recovery(self) -> None:
        result = self.optimize()
        amounts = [item.expected_recovered_amount for item in result.ranked_strategies]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        self.assertEqual(result.selected_strategy, result.ranked_strategies[0].strategy)

    def test_expected_recovery_equals_amount_times_probability(self) -> None:
        outcome = self.outcomes(self.optimize())["retry_now"]
        self.assertEqual(outcome.expected_recovered_amount, Decimal("8000") * outcome.success_probability)

    def test_results_expose_rationale_assumptions_and_friction(self) -> None:
        outcome = self.outcomes(self.optimize())["retry_now"]
        self.assertTrue(outcome.rationale)
        self.assertTrue(outcome.assumptions)
        self.assertEqual(outcome.estimated_customer_friction, "low")
        self.assertEqual(outcome.estimated_time_to_recovery, "immediate")

    def test_simulation_is_deterministic_for_same_input(self) -> None:
        values = {"payment_id": "pay_repeat", "failure_category": "temporary_network", "is_retryable": True}
        first = self.optimize(**values)
        second = self.optimizer.optimize_payment("pay_repeat")
        self.assertEqual([item.to_dict() for item in first.ranked_strategies], [item.to_dict() for item in second.ranked_strategies])


if __name__ == "__main__":
    unittest.main()
