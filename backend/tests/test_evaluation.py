"""Tests for the batch evaluation system."""

import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.evaluation import BatchEvaluator, EvaluationBaseline
from app.models import Payment
from app.repositories import PayFixRepository


class TestEvaluationBaseline(unittest.TestCase):
    """Tests for the EvaluationBaseline class."""

    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-baseline-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        self.baseline = EvaluationBaseline(self.repository)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def test_baseline_never_calls_optimizer(self) -> None:
        """Baseline must not call the PayFix optimizer."""
        payment = self._store_payment(
            payment_id="pay_no_opt",
            amount=Decimal("2000"),
            failure_category="temporary_network",
            risk_level="low",
        )

        with patch("app.optimizer.ExpectedRecoveryOptimizer") as mock_optimizer:
            self.baseline.evaluate_payment(payment.payment_id)
            mock_optimizer.assert_not_called()

    def test_baseline_selects_retry_now_only_when_eligible(self) -> None:
        """Baseline should pick retry_now iff EligibilityEngine says eligible."""
        # Case 1: eligible -> retry_now
        eligible_payment = self._store_payment(
            payment_id="pay_elig",
            amount=Decimal("1000"),
            failure_category="temporary_network",
            risk_level="low",
            retry_count=0,
            is_retryable=True,
        )
        result = self.baseline.evaluate_payment(eligible_payment.payment_id)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["strategy"], "retry_now")

        # Case 2: not eligible (high amount) -> baseline_no_action
        ineligible_payment = self._store_payment(
            payment_id="pay_inelig",
            amount=Decimal("20000"),
            failure_category="temporary_network",
            risk_level="low",
        )
        result = self.baseline.evaluate_payment(ineligible_payment.payment_id)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["strategy"], "baseline_no_action")
        self.assertEqual(result["recovered_amount"], Decimal("0"))

    def test_baseline_uses_same_synthetic_outcome_semantics(self) -> None:
        """Baseline must use StrategySimulator + 0.50 threshold (not 100% recovery)."""
        # Base 0.45 - 0.15 (insufficient_funds) = 0.30 -> below threshold -> zero recovery
        payment = self._store_payment(
            payment_id="pay_below_threshold",
            amount=Decimal("5000"),
            failure_category="insufficient_funds",
            risk_level="low",
            retry_count=0,
            successful_payment_count=2,
            failed_payment_count=0,
        )
        result = self.baseline.evaluate_payment(payment.payment_id)

        self.assertTrue(result["eligible"])
        self.assertEqual(result["strategy"], "retry_now")
        # Probability should be 0.30 (below 0.50) -> recovers ZERO, not full amount
        self.assertEqual(result["recovered_amount"], Decimal("0"))
        self.assertEqual(result["outcome"], "baseline_no_recovery")
        self.assertIsNotNone(result["success_probability"])
        self.assertLess(result["success_probability"], 0.50)

    def test_baseline_recovers_full_amount_when_probability_meets_threshold(self) -> None:
        """When simulator probability >= 0.50, baseline recovers full payment amount."""
        # temporary_failure (0.45 + 0.30) + history>=5 (+0.05) = 0.80
        payment = self._store_payment(
            payment_id="pay_above_threshold",
            amount=Decimal("3000"),
            failure_category="temporary_network",
            risk_level="low",
            retry_count=0,
            successful_payment_count=6,
            failed_payment_count=0,
        )
        result = self.baseline.evaluate_payment(payment.payment_id)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["strategy"], "retry_now")
        self.assertGreaterEqual(result["success_probability"], 0.50)
        self.assertEqual(result["recovered_amount"], Decimal("3000"))
        self.assertEqual(result["outcome"], "baseline_recovered")

    def test_baseline_payment_not_found(self) -> None:
        """Baseline raises PaymentNotFoundError for non-existent payment."""
        from app.evaluation import PaymentNotFoundError

        with self.assertRaises(PaymentNotFoundError):
            self.baseline.evaluate_payment("pay_nonexistent")

    def test_baseline_uses_actual_strategy_simulator(self) -> None:
        """Baseline must invoke the real StrategySimulator, not a hardcoded value."""
        payment = self._store_payment(
            payment_id="pay_real_sim",
            amount=Decimal("4000"),
            failure_category="temporary_network",
            risk_level="low",
            successful_payment_count=5,
        )
        with patch("app.strategy_simulator.StrategySimulator") as mock_sim_cls:
            mock_sim = mock_sim_cls.return_value
            from app.simulation_models import SimulatedStrategyOutcome

            mock_sim.simulate.return_value = [
                SimulatedStrategyOutcome(
                    "pay_real_sim",
                    "retry_now",
                    True,
                    Decimal("0.80"),
                    Decimal("3200"),
                    "low",
                    "immediate",
                    "Mock rationale",
                    ["Mock assumption"],
                )
            ]
            result = self.baseline.evaluate_payment(payment.payment_id)
            mock_cls_called = mock_sim_cls.called
            mock_simulate_called = mock_sim.simulate.called

        self.assertTrue(mock_cls_called)
        self.assertTrue(mock_simulate_called)
        self.assertEqual(result["success_probability"], 0.80)
        self.assertEqual(result["recovered_amount"], Decimal("4000"))

    def _store_payment(self, **overrides: object) -> Payment:
        """Store a test payment."""
        values: dict[str, object] = {
            "payment_id": f"pay_{uuid4().hex}",
            "customer_id": "customer_1",
            "merchant_id": "merchant_1",
            "amount": Decimal("1000"),
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


class TestBatchEvaluator(unittest.TestCase):
    """Tests for the BatchEvaluator class."""

    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-batch-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        self.evaluator = BatchEvaluator(self.repository)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def test_empty_batch(self) -> None:
        """Test evaluation with no payments to evaluate."""
        result = self.evaluator.run_evaluation()
        self.assertEqual(result["payments_evaluated"], 0)
        self.assertEqual(result["revenue_at_risk"], 0.0)
        self.assertEqual(result["baseline_recovered"], 0.0)
        self.assertEqual(result["payfix_recovered"], 0.0)

    def test_batch_size_limiting(self) -> None:
        """Test that batch size limits the number of payments evaluated."""
        for i in range(5):
            self._store_payment(
                payment_id=f"pay_batch_{i}",
                amount=Decimal(str(1000 + i * 100)),
            )
        result = self.evaluator.run_evaluation(batch_size=3)
        self.assertEqual(result["payments_evaluated"], 3)
        expected_revenue = Decimal("1000") + Decimal("1100") + Decimal("1200")
        self.assertEqual(result["revenue_at_risk"], float(expected_revenue))

    def test_already_recovered_payments_skipped(self) -> None:
        """Idempotency: already-recovered payments are not re-evaluated."""
        payment1 = self._store_payment(
            payment_id="pay_recovered",
            amount=Decimal("5000"),
        )
        self.repository.update_payment(
            payment1.payment_id,
            recovery_status="simulated_recovered",
            recovered_amount=Decimal("5000"),
        )
        payment2 = self._store_payment(
            payment_id="pay_unrecovered",
            amount=Decimal("3000"),
        )
        result = self.evaluator.run_evaluation()
        self.assertEqual(result["payments_evaluated"], 1)
        self.assertEqual(result["revenue_at_risk"], 3000.0)

    def test_baseline_independence_from_optimizer(self) -> None:
        """The baseline calculation itself must not depend on the optimizer.

        The new read-only BatchEvaluator legitimately uses
        ExpectedRecoveryOptimizer for the PayFix leg, but the baseline leg
        must compute its outcome without invoking any ranking/optimization.
        """
        payment = self._store_payment(
            payment_id="pay_batch_no_opt",
            amount=Decimal("2500"),
            failure_category="temporary_network",
            risk_level="low",
        )
        # Capture the baseline's own call count to the optimizer by patching
        # only the optimizer instance that EvaluationBaseline uses. Baseline
        # must never invoke `optimize_payment` or `optimize_from_snapshot`.
        with patch(
            "app.optimizer.ExpectedRecoveryOptimizer.optimize_from_snapshot",
            autospec=True,
        ) as mock_opt_snapshot, patch(
            "app.optimizer.ExpectedRecoveryOptimizer.optimize_payment",
            autospec=True,
        ) as mock_opt_id:
            self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
            # Baseline leg must not call the optimizer at all (by id or snapshot).
            self.assertEqual(mock_opt_id.call_count, 0)

        # Additionally, directly invoking EvaluationBaseline against the live
        # snapshot must not produce any optimizer side effects.
        with patch(
            "app.optimizer.ExpectedRecoveryOptimizer.optimize_from_snapshot",
            autospec=True,
        ) as mock_opt_snapshot, patch(
            "app.optimizer.ExpectedRecoveryOptimizer.optimize_payment",
            autospec=True,
        ) as mock_opt_id:
            baseline = EvaluationBaseline(self.repository)
            snapshot = self.repository.get_payment(payment.payment_id)
            baseline.evaluate_snapshot(snapshot, payment_id=payment.payment_id)
            mock_opt_snapshot.assert_not_called()
            mock_opt_id.assert_not_called()

    def test_baseline_uses_same_threshold_as_executor(self) -> None:
        """Baseline threshold constant must equal the executor's 0.50 threshold."""
        self.assertEqual(EvaluationBaseline.SUCCESS_THRESHOLD, Decimal("0.50"))

    def test_recovery_amounts_calculated_consistently(self) -> None:
        """Both baseline and PayFix should express amounts in the same Decimal scale."""
        payment = self._store_payment(
            payment_id="pay_consistent",
            amount=Decimal("1234.56"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        self.assertEqual(result["revenue_at_risk"], 1234.56)
        self.assertGreaterEqual(result["baseline_recovered"], 0.0)
        self.assertGreaterEqual(result["payfix_recovered"], 0.0)
        self.assertLessEqual(result["baseline_recovered"], result["revenue_at_risk"])
        self.assertLessEqual(result["payfix_recovered"], result["revenue_at_risk"])

    def test_uplift_calculation(self) -> None:
        """Uplift = payfix_recovered - baseline_recovered."""
        payment = self._store_payment(
            payment_id="pay_uplift",
            amount=Decimal("10000"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        expected_uplift = result["payfix_recovered"] - result["baseline_recovered"]
        self.assertEqual(result["recovered_revenue_uplift"], expected_uplift)
        if result["baseline_recovered"] > 0:
            expected_pct = (
                result["recovered_revenue_uplift"] / result["baseline_recovered"]
            ) * 100
            self.assertAlmostEqual(
                result["recovered_revenue_uplift_percentage"],
                expected_pct,
                places=6,
            )
        else:
            self.assertEqual(result["recovered_revenue_uplift_percentage"], 0.0)

    def test_recovery_rate_calculation(self) -> None:
        """Recovery rates are between 0 and 1."""
        payment = self._store_payment(
            payment_id="pay_rate",
            amount=Decimal("1000"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        self.assertGreaterEqual(result["baseline_recovery_rate"], 0.0)
        self.assertLessEqual(result["baseline_recovery_rate"], 1.0)
        self.assertGreaterEqual(result["payfix_recovery_rate"], 0.0)
        self.assertLessEqual(result["payfix_recovery_rate"], 1.0)

    def test_response_structure(self) -> None:
        """Response contains all required fields with correct types."""
        payment = self._store_payment(
            payment_id="pay_struct",
            amount=Decimal("2000"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        required = [
            "payments_evaluated", "revenue_at_risk", "baseline_recovered",
            "payfix_recovered", "successful_recoveries", "human_escalations",
            "stopped_cases", "blocked_actions", "baseline_recovery_rate",
            "payfix_recovery_rate", "recovered_revenue_uplift",
            "recovered_revenue_uplift_percentage", "strategy_metrics",
        ]
        for field in required:
            self.assertIn(field, result)
        self.assertIsInstance(result["payments_evaluated"], int)
        self.assertIsInstance(result["revenue_at_risk"], float)
        self.assertIsInstance(result["strategy_metrics"], dict)

    def test_strategy_metrics(self) -> None:
        """Strategy-level metrics are tracked."""
        payment = self._store_payment(
            payment_id="pay_strategy",
            amount=Decimal("5000"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        self.assertIsInstance(result["strategy_metrics"], dict)
        for data in result["strategy_metrics"].values():
            self.assertIn("count", data)
            self.assertIn("successful_recoveries", data)
            self.assertIn("recovered_amount", data)

    def test_escalation_counting(self) -> None:
        """human_escalations field is present and a non-negative int."""
        payment = self._store_payment(
            payment_id="pay_escalation",
            amount=Decimal("1500"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        self.assertIsInstance(result["human_escalations"], int)
        self.assertGreaterEqual(result["human_escalations"], 0)

    def test_stopped_case_counting(self) -> None:
        """stopped_cases field is present and a non-negative int."""
        payment = self._store_payment(
            payment_id="pay_stopped",
            amount=Decimal("2000"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        self.assertIsInstance(result["stopped_cases"], int)
        self.assertGreaterEqual(result["stopped_cases"], 0)

    def test_blocked_action_counting(self) -> None:
        """blocked_actions field is present and a non-negative int."""
        payment = self._store_payment(
            payment_id="pay_blocked",
            amount=Decimal("3000"),
        )
        result = self.evaluator.run_evaluation(payment_ids=[payment.payment_id])
        self.assertIsInstance(result["blocked_actions"], int)
        self.assertGreaterEqual(result["blocked_actions"], 0)

    def test_repeated_evaluation_no_double_counting(self) -> None:
        """Idempotency: payments explicitly marked as already recovered are
        skipped on subsequent auto-batches.

        Under the new READ-ONLY BatchEvaluator, evaluation never mutates the
        database. So the "already recovered" flag must be set explicitly
        before the run for the filter to take effect. This test pre-marks
        each payment's recovery state so we can assert the snapshot filter
        correctly excludes them.
        """
        # Pay already-recovered -> should be skipped by auto-batch.
        already = self._store_payment(
            payment_id="pay_already",
            amount=Decimal("5000"),
        )
        self.repository.update_payment(
            already.payment_id,
            recovery_status="simulated_recovered",
            recovered_amount=Decimal("5000"),
        )
        first = self.evaluator.run_evaluation()
        self.assertEqual(first["payments_evaluated"], 0)
        self.assertEqual(first["revenue_at_risk"], 0.0)

        # Pay explicitly marked as already recovered at the start -> excluded.
        pre_marked = self._store_payment(payment_id="pay_pre_marked", amount=Decimal("4000"))
        self.repository.update_payment(
            pre_marked.payment_id,
            recovery_status="simulated_recovered",
            recovered_amount=Decimal("4000"),
        )

        # Add an unrecovered payment + explicitly mark another as recovered.
        self._store_payment(payment_id="pay_fresh", amount=Decimal("3000"))
        unrecovered_explicit = self._store_payment(
            payment_id="pay_unrecovered_explicit", amount=Decimal("2500")
        )

        # Run twice in a row. Because evaluation is read-only, BOTH runs
        # must produce identical metrics — no state is consumed.
        second = self.evaluator.run_evaluation()
        third = self.evaluator.run_evaluation()

        self.assertEqual(second["payments_evaluated"], 2)  # pay_fresh + pay_unrecovered_explicit
        self.assertEqual(third["payments_evaluated"], 2)   # same — no state was consumed
        self.assertEqual(
            second["revenue_at_risk"],
            third["revenue_at_risk"],
        )
        self.assertEqual(
            second["payfix_recovered"],
            third["payfix_recovered"],
        )
        self.assertEqual(
            second["baseline_recovered"],
            third["baseline_recovered"],
        )

        # Database state must still be unchanged for all payments.
        fresh_row = self.repository.get_payment("pay_fresh")
        self.assertEqual(fresh_row.get("recovery_status"), "not_started")
        self.assertEqual(fresh_row.get("recovered_amount"), 0)

    def test_invalid_batch_size(self) -> None:
        """Invalid batch sizes are clamped to 0."""
        self._store_payment(payment_id="pay_inv1", amount=Decimal("1000"))
        self._store_payment(payment_id="pay_inv2", amount=Decimal("2000"))
        result_zero = self.evaluator.run_evaluation(batch_size=0)
        self.assertEqual(result_zero["payments_evaluated"], 0)
        result_neg = self.evaluator.run_evaluation(batch_size=-1)
        self.assertEqual(result_neg["payments_evaluated"], 0)

    def _store_payment(self, **overrides: object) -> Payment:
        """Store a test payment."""
        values: dict[str, object] = {
            "payment_id": overrides.get("payment_id", f"pay_{uuid4().hex}"),
            "customer_id": "customer_1",
            "merchant_id": "merchant_1",
            "amount": Decimal("1000"),
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


class TestBatchEvaluatorReadOnly(unittest.TestCase):
    """Tests that prove POST /evaluation/run is fully READ-ONLY and deterministic."""

    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-readonly-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        self.evaluator = BatchEvaluator(self.repository)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def _seed_mixed_batch(self, count: int = 20) -> None:
        """Seed a small, varied synthetic batch."""
        for i in range(count):
            amount = Decimal(str(1000 + (i * 250)))
            failure_category = (
                "temporary_network"
                if i % 3 == 0
                else "insufficient_funds"
                if i % 3 == 1
                else "expired_card"
            )
            self._store_payment(
                payment_id=f"pay_seed_{i}",
                amount=amount,
                failure_category=failure_category,
                risk_level="low" if i % 5 != 0 else "high",
                successful_payment_count=5,
                failed_payment_count=1,
                available_payment_methods=["upi", "card"] if i % 2 == 0 else ["upi"],
            )

    def test_evaluation_does_not_mutate_payment_state(self) -> None:
        """Eval must not change retry_count, contact_count, recovery_status, or recovered_amount."""
        self._seed_mixed_batch(10)
        before = {
            pid: dict(self.repository.get_payment(pid))
            for pid in [p["payment_id"] for p in self.repository.list_payments()]
        }

        self.evaluator.run_evaluation(batch_size=100)

        for pid, row in before.items():
            after_row = self.repository.get_payment(pid)
            self.assertEqual(
                after_row.get("retry_count"),
                row.get("retry_count"),
                f"retry_count mutated for {pid}",
            )
            self.assertEqual(
                after_row.get("customer_contact_count"),
                row.get("customer_contact_count"),
                f"customer_contact_count mutated for {pid}",
            )
            self.assertEqual(
                after_row.get("recovery_status"),
                row.get("recovery_status"),
                f"recovery_status mutated for {pid}",
            )
            self.assertEqual(
                after_row.get("recovered_amount"),
                row.get("recovered_amount"),
                f"recovered_amount mutated for {pid}",
            )

    def test_evaluation_does_not_create_recovery_attempts(self) -> None:
        """No recovery_attempt rows must be inserted by an evaluation run."""
        self._seed_mixed_batch(10)
        before_ids = self._all_attempt_payment_ids()
        self.evaluator.run_evaluation(batch_size=100)
        after_ids = self._all_attempt_payment_ids()
        self.assertEqual(before_ids, after_ids)

    def test_evaluation_does_not_create_audit_logs(self) -> None:
        """No audit_log rows must be inserted by an evaluation run."""
        self._seed_mixed_batch(10)
        before_count = self._audit_log_count()
        self.evaluator.run_evaluation(batch_size=100)
        after_count = self._audit_log_count()
        self.assertEqual(before_count, after_count)

    def test_repeated_evaluation_is_deterministic(self) -> None:
        """Running evaluation twice on the same dataset yields identical metrics."""
        self._seed_mixed_batch(25)
        first = self.evaluator.run_evaluation(batch_size=100)
        second = self.evaluator.run_evaluation(batch_size=100)

        # Convert nested Decimals to floats for comparison.
        self.assertEqual(
            _metrics_for_compare(first),
            _metrics_for_compare(second),
        )

    def test_payfix_produces_successful_recoveries_on_seeded_batch(self) -> None:
        """With a healthy mixed batch, PayFix should produce at least some successes."""
        self._seed_mixed_batch(30)
        result = self.evaluator.run_evaluation(batch_size=100)
        self.assertGreater(result["successful_recoveries"], 0)
        self.assertGreater(result["payfix_recovered"], 0.0)

    def test_high_risk_payments_are_not_automatically_recovered(self) -> None:
        """Suspected-fraud / high-risk payments must not be automated-recovered."""
        # Seed 5 fraud / high-risk payments.
        for i in range(5):
            self._store_payment(
                payment_id=f"pay_fraud_{i}",
                amount=Decimal("3000"),
                failure_category="suspected_fraud",
                risk_level="high",
                successful_payment_count=10,
                failed_payment_count=0,
                available_payment_methods=["upi", "card"],
            )
        # Seed 5 normal payments that should produce recoveries.
        for i in range(5):
            self._store_payment(
                payment_id=f"pay_norm_{i}",
                amount=Decimal("2000"),
                failure_category="temporary_network",
                risk_level="low",
                successful_payment_count=8,
                failed_payment_count=0,
                available_payment_methods=["upi", "card"],
            )

        result = self.evaluator.run_evaluation(batch_size=100)

        # Recoveries should not all come from the fraud set; eligibility must
        # prevent automated strategies from being applied to high-risk fraud.
        # We assert by checking that the fraud payments do not end up with
        # `simulated_recovered` as their outcome via a targeted recovery call.
        # Eval is read-only, so verify via per-payment eligibility inspection.
        from app.eligibility import EligibilityEngine

        engine = EligibilityEngine()
        for pid in [f"pay_fraud_{i}" for i in range(5)]:
            row = self.repository.get_payment(pid)
            eligibility = engine.evaluate(row)
            retry_eligible = any(
                e.strategy == "retry_now" and e.eligible for e in eligibility
            )
            apm_eligible = any(
                e.strategy == "alternate_payment_method" and e.eligible for e in eligibility
            )
            # The recovery for fraud should not be authorized to an
            # automated retry or alternate payment method; only human
            # escalation or stop should remain.
            self.assertFalse(retry_eligible, f"fraud payment {pid} should not allow retry_now")
            self.assertFalse(apm_eligible, f"fraud payment {pid} should not allow alternate_payment_method")

    def test_baseline_and_payfix_metrics_are_independent(self) -> None:
        """Baseline and PayFix metrics must be calculated from their own simulations."""
        self._seed_mixed_batch(20)
        result = self.evaluator.run_evaluation(batch_size=100)

        # Both metrics must be present and bounded.
        self.assertGreaterEqual(result["baseline_recovered"], 0.0)
        self.assertGreaterEqual(result["payfix_recovered"], 0.0)
        self.assertLessEqual(
            result["baseline_recovered"], result["revenue_at_risk"]
        )
        self.assertLessEqual(
            result["payfix_recovered"], result["revenue_at_risk"]
        )

        # Uplift must equal payfix - baseline.
        self.assertAlmostEqual(
            result["recovered_revenue_uplift"],
            result["payfix_recovered"] - result["baseline_recovered"],
            places=6,
        )

    # --- helpers ---

    def _store_payment(self, **overrides: object) -> Payment:
        values: dict[str, object] = {
            "payment_id": overrides.get("payment_id", f"pay_{uuid4().hex}"),
            "customer_id": "customer_1",
            "merchant_id": "merchant_1",
            "amount": Decimal("1000"),
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

    def _all_attempt_payment_ids(self) -> set[str]:
        ids: set[str] = set()
        for pid in [p["payment_id"] for p in self.repository.list_payments()]:
            ids.update(a["payment_id"] for a in self.repository.list_recovery_attempts(pid))
        return ids

    def _audit_log_count(self) -> int:
        total = 0
        for pid in [p["payment_id"] for p in self.repository.list_payments()]:
            total += len(self.repository.list_audit_logs(pid))
        return total


def _metrics_for_compare(result: dict[str, object]) -> dict[str, object]:
    """Normalize a metrics dict for deterministic comparison."""
    out = {k: v for k, v in result.items() if k != "strategy_metrics"}
    strategy_metrics = result.get("strategy_metrics", {})
    out["strategy_metrics"] = {
        strategy: (
            data["count"],
            data["successful_recoveries"],
            round(float(data["recovered_amount"]), 2),
        )
        for strategy, data in strategy_metrics.items()
    }
    return out


if __name__ == "__main__":
    unittest.main()
