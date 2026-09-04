"""Tests for the bounded, synthetic end-to-end recovery workflow."""

import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.diagnosis_providers import DeterministicDiagnosisProvider
from app.diagnosis_service import DiagnosisService, PaymentNotFoundError
from app.main import recover_payment
from app.models import Payment
from app.optimizer import ExpectedRecoveryOptimizer
from app.recovery_service import RecoveryService
from app.repositories import PayFixRepository
from app.simulation_models import OptimizationResult, SimulatedStrategyOutcome
from app.strategy_simulator import SIMULATION_VERSION


class ForcedOptimizer:
    """Test double that preserves ranked outcomes except for a forced first recommendation."""

    def __init__(self, inner: ExpectedRecoveryOptimizer, forced: SimulatedStrategyOutcome) -> None:
        self.inner = inner
        self.forced = forced

    def optimize_payment(self, payment_id: str) -> OptimizationResult:
        result = self.inner.optimize_payment(payment_id)
        ranked = [self.forced, *[item for item in result.ranked_strategies if item.strategy != self.forced.strategy]]
        evaluated = [self.forced, *[item for item in result.strategies_evaluated if item.strategy != self.forced.strategy]]
        return OptimizationResult(
            payment_id,
            evaluated,
            ranked,
            self.forced.strategy,
            self.forced.expected_recovered_amount,
            f"{self.forced.strategy} was forced as the optimizer recommendation.",
            result.simulation_version,
        )


class StopOnlyOptimizer:
    def optimize_payment(self, payment_id: str) -> OptimizationResult:
        stop = SimulatedStrategyOutcome(
            payment_id,
            "stop",
            True,
            Decimal("0"),
            Decimal("0"),
            "none",
            "no further action",
            "Forced stop recommendation.",
            ["Test double."],
        )
        return OptimizationResult(payment_id, [stop], [stop], "stop", Decimal("0"), "Forced stop.", SIMULATION_VERSION)


class RecoveryWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-recovery-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        diagnosis_service = DiagnosisService(self.repository, DeterministicDiagnosisProvider())
        self.optimizer = ExpectedRecoveryOptimizer(self.repository, diagnosis_service)
        self.service = RecoveryService(self.repository, diagnosis_service, self.optimizer)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def store(self, **overrides: object) -> Payment:
        values: dict[str, object] = {
            "payment_id": f"pay_{uuid4().hex}",
            "customer_id": "customer_1",
            "merchant_id": "merchant_1",
            "amount": Decimal("8000"),
            "payment_status": "failed",
            "payment_method": "upi",
            "available_payment_methods": ["upi"],
            "failure_category": "temporary_network",
            "failure_reason": "Issuer timeout",
            "is_retryable": True,
            "risk_level": "low",
            "successful_payment_count": 3,
            "failed_payment_count": 0,
            "retry_count": 0,
            "customer_contact_count": 0,
        }
        values.update(overrides)
        payment = Payment(**values)  # type: ignore[arg-type]
        self.repository.create_payment(payment)
        return payment

    def test_successful_synthetic_recovery(self) -> None:
        payment = self.store()
        decision = self.service.recover_payment(payment.payment_id)
        self.assertTrue(decision.execution.execution_allowed)
        self.assertEqual(decision.execution.simulated_outcome, "simulated_recovered")
        self.assertEqual(decision.execution.recovered_amount, Decimal("8000"))
        self.assertNotIn(decision.selected_strategy, {"stop", "human_escalation"})

    def test_guardrail_blocked_optimizer_choice_falls_back_safely(self) -> None:
        payment = self.store(amount=Decimal("20000"))
        forced = SimulatedStrategyOutcome(
            payment.payment_id,
            "payment_link",
            True,
            Decimal("0.90"),
            Decimal("18000"),
            "moderate",
            "24 hours",
            "Forced high-value optimizer pick.",
            ["Test double."],
        )
        service = RecoveryService(self.repository, self.service.diagnosis_service, ForcedOptimizer(self.optimizer, forced))
        decision = service.recover_payment(payment.payment_id)
        self.assertNotEqual(decision.selected_strategy, "payment_link")
        self.assertIn(decision.selected_strategy, {"human_escalation", "stop"})
        self.assertNotEqual(decision.execution.simulated_outcome, "simulated_recovered")
        self.assertEqual(decision.execution.recovered_amount, Decimal("0"))
        self.assertIn("guardrails blocked", decision.selection_reason.lower())

    def test_fraud_high_risk_never_performs_automated_recovery(self) -> None:
        payment = self.store(failure_category="suspected_fraud", failure_reason="Fraud check", risk_level="high", is_retryable=False)
        decision = self.service.recover_payment(payment.payment_id)
        self.assertEqual(decision.selected_strategy, "human_escalation")
        self.assertEqual(decision.execution.simulated_outcome, "simulated_human_review_queued")
        self.assertEqual(decision.execution.recovered_amount, Decimal("0"))
        saved = self.repository.get_payment(payment.payment_id)
        self.assertEqual(saved["retry_count"], 0)
        self.assertEqual(saved["customer_contact_count"], 0)

    def test_high_value_payment_does_not_bypass_amount_limit(self) -> None:
        payment = self.store(amount=Decimal("20000"))
        decision = self.service.recover_payment(payment.payment_id)
        self.assertIn(decision.selected_strategy, {"human_escalation", "stop"})
        self.assertNotIn(decision.selected_strategy, {"retry_now", "retry_later", "payment_link", "customer_reminder", "alternate_payment_method"})
        self.assertEqual(decision.execution.recovered_amount, Decimal("0"))
        saved = self.repository.get_payment(payment.payment_id)
        self.assertEqual(Decimal(str(saved["recovered_amount"])), Decimal("0"))

    def test_retry_and_contact_limits_are_respected(self) -> None:
        retried = self.store(retry_count=2)
        retry_decision = self.service.recover_payment(retried.payment_id)
        self.assertNotIn(retry_decision.selected_strategy, {"retry_now", "retry_later"})

        contacted = self.store(customer_contact_count=2)
        contact_decision = self.service.recover_payment(contacted.payment_id)
        self.assertNotIn(contact_decision.selected_strategy, {"payment_link", "customer_reminder"})

    def test_stop_strategy_performs_no_action(self) -> None:
        payment = self.store()
        service = RecoveryService(self.repository, self.service.diagnosis_service, StopOnlyOptimizer())
        decision = service.recover_payment(payment.payment_id)
        self.assertEqual(decision.selected_strategy, "stop")
        self.assertEqual(decision.execution.simulated_outcome, "no_action")
        self.assertEqual(decision.execution.recovered_amount, Decimal("0"))
        saved = self.repository.get_payment(payment.payment_id)
        self.assertEqual(saved["recovery_status"], "stopped")
        self.assertEqual(saved["retry_count"], 0)
        self.assertEqual(saved["payment_status"], "failed")

    def test_human_escalation_queues_simulated_review(self) -> None:
        payment = self.store(failure_category="permanent_decline", is_retryable=False, retry_count=2, customer_contact_count=2)
        decision = self.service.recover_payment(payment.payment_id)
        self.assertEqual(decision.selected_strategy, "human_escalation")
        self.assertEqual(decision.execution.simulated_outcome, "simulated_human_review_queued")
        self.assertEqual(decision.execution.recovered_amount, Decimal("0"))
        saved = self.repository.get_payment(payment.payment_id)
        self.assertEqual(saved["recovery_status"], "human_review")

    def test_payment_attempt_and_audit_are_persisted(self) -> None:
        payment = self.store()
        decision = self.service.recover_payment(payment.payment_id)
        saved = self.repository.get_payment(payment.payment_id)
        self.assertEqual(saved["recovery_status"], "simulated_recovered")
        self.assertEqual(Decimal(str(saved["recovered_amount"])), Decimal("8000"))
        self.assertEqual(saved["payment_status"], "succeeded")
        self.assertGreaterEqual(saved["retry_count"], 1)

        attempts = self.repository.list_recovery_attempts(payment.payment_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["strategy"], decision.selected_strategy)
        self.assertEqual(attempts[0]["status"], "completed")
        self.assertEqual(attempts[0]["result"], "simulated_recovered")

        audits = self.repository.list_audit_logs(payment.payment_id)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["event_type"], "recovery_executed")
        self.assertEqual(audits[0]["selected_action"], decision.selected_strategy)
        self.assertTrue(audits[0]["diagnosis"])
        self.assertTrue(audits[0]["strategies_considered"])
        self.assertTrue(audits[0]["action_rationale"])
        self.assertTrue(audits[0]["guardrails_passed"])
        self.assertEqual(audits[0]["execution_result"], "simulated_recovered")
        self.assertEqual(Decimal(str(audits[0]["recovered_amount"])), Decimal("8000"))

    def test_recovered_amount_is_not_double_counted(self) -> None:
        payment = self.store()
        first = self.service.recover_payment(payment.payment_id)
        second = self.service.recover_payment(payment.payment_id)
        self.assertEqual(first.execution.recovered_amount, Decimal("8000"))
        self.assertEqual(second.execution.recovered_amount, Decimal("8000"))
        saved = self.repository.get_payment(payment.payment_id)
        self.assertEqual(Decimal(str(saved["recovered_amount"])), Decimal("8000"))
        self.assertEqual(len(self.repository.list_recovery_attempts(payment.payment_id)), 1)
        self.assertEqual(len(self.repository.list_audit_logs(payment.payment_id)), 1)
        self.assertIn("already completed", second.selection_reason.lower())

    def test_unknown_payment_raises_and_http_returns_404(self) -> None:
        with self.assertRaises(PaymentNotFoundError):
            self.service.recover_payment("pay_missing")
        with self.assertRaises(HTTPException) as context:
            recover_payment("pay_missing")
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Payment not found.")

    def test_expired_payment_method_does_not_retry(self) -> None:
        payment = self.store(failure_category="expired_payment_method", failure_reason="Card expired", is_retryable=False)
        decision = self.service.recover_payment(payment.payment_id)
        self.assertNotIn(decision.selected_strategy, {"retry_now", "retry_later"})


if __name__ == "__main__":
    unittest.main()
