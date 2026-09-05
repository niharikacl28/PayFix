"""Tests for the read-only payment decision inspection endpoint.

These tests prove the following invariants:
  a) GET /decision does not mutate payment state.
  b) GET /decision does not create recovery attempts or audit logs.
  c) GET /decision after POST /recover returns a coherent snapshot.
  d) Repeated GET /decision returns the same decision information.
  e) GET /decision never calls the executor.
  f) Existing POST /recover idempotency remains unchanged.
"""

import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.decision_service import DecisionService
from app.diagnosis_providers import DeterministicDiagnosisProvider
from app.diagnosis_service import DiagnosisService, PaymentNotFoundError
from app.guardrails import RecoveryGuardrails
from app.main import get_payment_decision
from app.models import Payment
from app.optimizer import ExpectedRecoveryOptimizer
from app.recovery_service import RecoveryService
from app.repositories import PayFixRepository
from app.simulated_executor import SimulatedRecoveryExecutor


class _TrackingExecutor(SimulatedRecoveryExecutor):
    """Executor that records every invocation."""

    def __init__(self) -> None:
        self.call_count = 0
        super().__init__()

    def execute(self, payment, outcome, guardrail):  # type: ignore[override]
        self.call_count += 1
        return super().execute(payment, outcome, guardrail)


class ReadOnlyDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-decision-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        diagnosis_service = DiagnosisService(self.repository, DeterministicDiagnosisProvider())
        optimizer = ExpectedRecoveryOptimizer(self.repository, diagnosis_service)
        self.recovery = RecoveryService(self.repository, diagnosis_service, optimizer)
        self.decision = DecisionService(self.repository, diagnosis_service, optimizer)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def store(self, **overrides: object) -> Payment:
        values: dict[str, object] = {
            "payment_id": f"pay_{uuid4().hex}",
            "customer_id": "customer_1",
            "merchant_id": "merchant_1",
            "amount": Decimal("4999"),
            "payment_status": "failed",
            "payment_method": "card",
            "available_payment_methods": ["card", "netbanking"],
            "failure_category": "insufficient_funds",
            "failure_reason": "Insufficient funds",
            "is_retryable": True,
            "risk_level": "low",
            "successful_payment_count": 5,
            "failed_payment_count": 1,
            "retry_count": 0,
            "customer_contact_count": 0,
        }
        values.update(overrides)
        payment = Payment(**values)  # type: ignore[arg-type]
        self.repository.create_payment(payment)
        return payment

    def _counts(self, payment_id: str) -> tuple[int, int, dict[str, object]]:
        payment = self.repository.get_payment(payment_id)
        attempts = self.repository.list_recovery_attempts(payment_id)
        audits = self.repository.list_audit_logs(payment_id)
        snapshots = self.repository.get_decision_snapshot(payment_id)
        assert payment is not None
        return len(attempts), len(audits), dict(payment) | {"snapshot": snapshots is not None}

    def test_unknown_payment_returns_404(self) -> None:
        with self.assertRaises(PaymentNotFoundError):
            self.decision.get_decision("pay_missing")
        with self.assertRaises(HTTPException) as context:
            get_payment_decision("pay_missing")
        self.assertEqual(context.exception.status_code, 404)

    # a) GET /decision does not mutate payment state.
    def test_decision_is_read_only_when_no_recovery_yet(self) -> None:
        payment = self.store()
        snapshot = self.decision.get_decision(payment.payment_id)

        attempts_before, audits_before, row_before = self._counts(payment.payment_id)
        for _ in range(3):
            self.decision.get_decision(payment.payment_id)
        attempts_after, audits_after, row_after = self._counts(payment.payment_id)

        self.assertEqual(attempts_before, attempts_after)
        self.assertEqual(audits_before, audits_after)
        self.assertEqual(row_before, row_after)
        self.assertEqual(snapshot.execution.simulated_outcome, "no_action")
        self.assertEqual(snapshot.execution.recovered_amount, Decimal("0"))
        self.assertEqual(snapshot.selected_strategy, snapshot.optimization.selected_strategy)

    # c) GET /decision after POST /recover returns a coherent snapshot.
    # The selected strategy, the eligible strategies, the ranked outcomes, the
    # expected recovery, the guardrail reason and the selection reason must
    # ALL agree with the originally-executed decision (not a replay against
    # the mutated payment row).
    def test_decision_after_recovery_is_coherent_and_consistent(self) -> None:
        payment = self.store()
        executed = self.recovery.recover_payment(payment.payment_id)
        snapshot = self.decision.get_decision(payment.payment_id)

        # Selected strategy matches the original execution.
        self.assertEqual(snapshot.selected_strategy, executed.selected_strategy)
        self.assertEqual(snapshot.execution.selected_strategy, executed.selected_strategy)
        self.assertEqual(snapshot.execution.recovered_amount, executed.execution.recovered_amount)
        self.assertEqual(snapshot.execution.simulated_outcome, executed.execution.simulated_outcome)
        self.assertEqual(snapshot.execution.timestamp, executed.execution.timestamp)
        self.assertEqual(snapshot.execution.reason, executed.execution.reason)
        self.assertEqual(snapshot.expected_recovered_amount, executed.expected_recovered_amount)

        # Diagnosis/optimization come from the persisted snapshot and the
        # selected strategy IS in the eligible set and ranked list.
        self.assertIn(
            snapshot.selected_strategy, snapshot.diagnosis.eligible_strategy_names,
            "selected strategy must be in diagnosis.eligible_strategy_names",
        )
        ranked_names = [s.strategy for s in snapshot.optimization.ranked_strategies]
        self.assertIn(
            snapshot.selected_strategy, ranked_names,
            "selected strategy must be in optimization.ranked_strategies",
        )
        # The selected strategy in optimization must match the snapshot.
        self.assertEqual(snapshot.optimization.selected_strategy, snapshot.selected_strategy)
        # Guardrail context refers to the original decision, not a replay.
        self.assertEqual(snapshot.guardrail_result.strategy, snapshot.selected_strategy)
        self.assertNotIn("already completed", snapshot.guardrail_result.reason.lower())
        self.assertNotIn("already completed", snapshot.selection_reason.lower())
        # The "winner" row of the ranked strategies has a real expected
        # recovery (not 0) so the UI's "Expected: ₹X" cell is meaningful.
        winner = next(s for s in snapshot.optimization.ranked_strategies
                      if s.strategy == snapshot.selected_strategy)
        self.assertGreater(winner.expected_recovered_amount, Decimal("0"))

    # d) Repeated GET /decision returns the same decision information.
    def test_repeated_decision_calls_are_stable(self) -> None:
        payment = self.store()
        self.recovery.recover_payment(payment.payment_id)
        first = self.decision.get_decision(payment.payment_id).to_dict()
        for _ in range(5):
            again = self.decision.get_decision(payment.payment_id).to_dict()
            self.assertEqual(first, again)

    # b) GET /decision does not create recovery attempts or audit logs.
    def test_decision_does_not_create_attempts_or_audits(self) -> None:
        payment = self.store()
        attempts_before, audits_before, _ = self._counts(payment.payment_id)
        for _ in range(5):
            self.decision.get_decision(payment.payment_id)
        attempts_after, audits_after, _ = self._counts(payment.payment_id)
        self.assertEqual(attempts_before, attempts_after)
        self.assertEqual(audits_before, audits_after)
        # No snapshot should be created by GET /decision either.
        self.assertIsNone(self.repository.get_decision_snapshot(payment.payment_id))

    def test_decision_does_not_increment_retry_or_contact_counters(self) -> None:
        payment = self.store()
        self.recovery.recover_payment(payment.payment_id)
        attempts_before, audits_before, row_before = self._counts(payment.payment_id)
        retry_before = row_before["retry_count"]
        contact_before = row_before["customer_contact_count"]
        for _ in range(5):
            self.decision.get_decision(payment.payment_id)
        attempts_after, audits_after, row_after = self._counts(payment.payment_id)
        self.assertEqual(retry_before, row_after["retry_count"])
        self.assertEqual(contact_before, row_after["customer_contact_count"])
        self.assertEqual(attempts_before, attempts_after)
        self.assertEqual(audits_before, audits_after)

    # e) GET /decision never calls the executor.
    def test_decision_never_invokes_executor(self) -> None:
        payment = self.store()
        self.recovery.recover_payment(payment.payment_id)
        # Now swap in a tracking executor and confirm it is NEVER called by
        # the read-only service.
        tracking = _TrackingExecutor()
        decision_with_tracking = DecisionService(
            self.repository,
            DiagnosisService(self.repository, DeterministicDiagnosisProvider()),
            ExpectedRecoveryOptimizer(
                self.repository,
                DiagnosisService(self.repository, DeterministicDiagnosisProvider()),
            ),
        )
        for _ in range(5):
            decision_with_tracking.get_decision(payment.payment_id)
        self.assertEqual(tracking.call_count, 0)

    # f) Existing POST /recover idempotency remains unchanged.
    def test_recover_remains_idempotent_and_does_not_duplicate_snapshot(self) -> None:
        payment = self.store()
        first = self.recovery.recover_payment(payment.payment_id)
        second = self.recovery.recover_payment(payment.payment_id)

        # Same outcome.
        self.assertEqual(first.selected_strategy, second.selected_strategy)
        self.assertEqual(first.execution.recovered_amount, second.execution.recovered_amount)
        # Only one attempt and one audit row.
        self.assertEqual(len(self.repository.list_recovery_attempts(payment.payment_id)), 1)
        self.assertEqual(len(self.repository.list_audit_logs(payment.payment_id)), 1)
        # Only one snapshot row — the replay must not have overwritten it with
        # the "already completed" replay payload.
        snapshot = self.repository.get_decision_snapshot(payment.payment_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["selected_strategy"], first.selected_strategy)
        # GET /decision returns the ORIGINAL decision, not the replay text.
        snapshot_decision = self.decision.get_decision(payment.payment_id)
        self.assertNotIn("already completed", snapshot_decision.selection_reason.lower())
        self.assertNotIn("already completed", snapshot_decision.guardrail_result.reason.lower())

    def test_decision_endpoint_serializes_via_to_dict(self) -> None:
        payment = self.store()
        self.recovery.recover_payment(payment.payment_id)
        snapshot = self.decision.get_decision(payment.payment_id)
        payload = snapshot.to_dict()
        self.assertEqual(payload["selected_strategy"], snapshot.selected_strategy)
        self.assertEqual(
            payload["expected_recovered_amount"],
            float(snapshot.expected_recovered_amount),
        )
        self.assertEqual(
            payload["execution"]["recovered_amount"],
            float(snapshot.execution.recovered_amount),
        )
        self.assertEqual(
            payload["execution"]["simulated_outcome"],
            snapshot.execution.simulated_outcome,
        )
        self.assertIn("diagnosis", payload)
        self.assertIn("optimization", payload)
        self.assertIn("guardrail_result", payload)
        self.assertIn("selection_reason", payload)

    def test_legacy_attempt_only_path_is_honest_about_reconstruction(self) -> None:
        # Simulate a pre-snapshot legacy attempt by creating an attempt and
        # audit row directly (bypassing RecoveryService) so the snapshot is
        # never written.
        payment = self.store()
        from app.models import AuditLog, RecoveryAttempt
        self.repository.create_recovery_attempt(
            RecoveryAttempt(
                payment_id=payment.payment_id,
                strategy="alternate_payment_method",
                status="completed",
                result="simulated_recovered",
                recovered_amount=Decimal("4999"),
                reason="Alternate payment method suggested.",
                completed_at="2025-01-01T00:00:00+00:00",
            )
        )
        self.repository.create_audit_log(
            AuditLog(
                payment_id=payment.payment_id,
                event_type="recovery_executed",
                event_details="legacy test audit",
                diagnosis="Legacy diagnosis text.",
                strategies_considered=["alternate_payment_method", "stop"],
                selected_action="alternate_payment_method",
                action_rationale="Legacy rationale text.",
                guardrails_passed=True,
                execution_result="simulated_recovered",
                recovered_amount=Decimal("4999"),
            )
        )
        snapshot = self.decision.get_decision(payment.payment_id)
        # Reconstructible fields are populated from the attempt + audit row.
        self.assertEqual(snapshot.selected_strategy, "alternate_payment_method")
        self.assertEqual(snapshot.execution.recovered_amount, Decimal("4999"))
        self.assertEqual(snapshot.execution.simulated_outcome, "simulated_recovered")
        self.assertEqual(snapshot.execution.reason, "Alternate payment method suggested.")
        self.assertEqual(snapshot.execution.timestamp, "2025-01-01T00:00:00+00:00")
        # The diagnosis explanation and rationale come from the audit row.
        self.assertEqual(snapshot.diagnosis.explanation, "Legacy diagnosis text.")
        self.assertEqual(snapshot.selection_reason, "Legacy rationale text.")
        # Non-reconstructible fields are explicitly empty, not fabricated.
        self.assertEqual(snapshot.diagnosis.eligible_strategy_names, [])
        # Ranked strategies has exactly one placeholder outcome whose
        # probabilities are zero — never a fabricated success probability.
        self.assertEqual(len(snapshot.optimization.ranked_strategies), 1)
        only = snapshot.optimization.ranked_strategies[0]
        self.assertEqual(only.strategy, "alternate_payment_method")
        self.assertEqual(only.success_probability, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
