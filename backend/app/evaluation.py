"""Batch evaluation system for synthetic payment recovery."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Mapping

from .models import Payment
from .repositories import PayFixRepository


class EvaluationBaseline:
    """Naive recovery baseline: always retry_now when eligible, otherwise zero.

    Strategy selection is naive (no optimizer, no ranking).
    Outcome model is shared with PayFix: uses the existing StrategySimulator to compute
    retry_now's synthetic success probability and the same executor threshold
    (success_probability >= 0.50) to determine whether the full amount is recovered.
    """

    SUCCESS_THRESHOLD = Decimal("0.50")

    def __init__(self, repository: PayFixRepository) -> None:
        self.repository = repository

    def evaluate_payment(self, payment_id: str) -> Dict[str, Any]:
        """Apply baseline policy against the live repository row."""
        payment = self.repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment '{payment_id}' was not found.")
        return self.evaluate_snapshot(payment, payment_id=payment_id)

    def evaluate_snapshot(
        self,
        snapshot: Mapping[str, Any],
        payment_id: str | None = None,
    ) -> Dict[str, Any]:
        """Apply baseline policy against an in-memory snapshot.

        Steps:
        1. Build eligibility via the existing EligibilityEngine against the snapshot.
        2. If retry_now is eligible, build the diagnosis via DiagnosisService
           from the snapshot (no repository reads) and run the existing
           StrategySimulator to obtain retry_now's SimulatedStrategyOutcome.
        3. Apply the same executor threshold as PayFix: if success_probability
           >= 0.50, recover the full amount; otherwise recover zero.
        4. If retry_now is not eligible, recover zero.
        """
        resolved_id = payment_id or str(snapshot.get("payment_id", ""))

        # Step 1: eligibility check (same engine PayFix uses)
        from .eligibility import EligibilityEngine
        from .strategy_simulator import StrategySimulator
        from .diagnosis_service import DiagnosisService

        eligibility_engine = EligibilityEngine()
        eligibility = eligibility_engine.evaluate(snapshot)
        retry_eligible = any(
            item.strategy == "retry_now" and item.eligible for item in eligibility
        )

        if not retry_eligible:
            return {
                "payment_id": resolved_id,
                "strategy": "baseline_no_action",
                "recovered_amount": Decimal("0"),
                "outcome": "baseline_no_action",
                "eligible": False,
                "success_probability": None,
            }

        # Step 2: build diagnosis from snapshot (no repository reads)
        diagnosis_service = DiagnosisService(self.repository)
        diagnosis = diagnosis_service.diagnose_from_snapshot(snapshot, payment_id=resolved_id)

        # Step 3: run the existing StrategySimulator for retry_now ONLY
        simulator = StrategySimulator()
        outcomes = simulator.simulate(snapshot, diagnosis, eligibility)
        retry_outcome = next((o for o in outcomes if o.strategy == "retry_now"), None)
        if retry_outcome is None:
            return {
                "payment_id": resolved_id,
                "strategy": "baseline_no_action",
                "recovered_amount": Decimal("0"),
                "outcome": "baseline_no_action",
                "eligible": True,
                "success_probability": None,
            }

        # Step 4: apply the same executor threshold semantics
        amount = Decimal(str(snapshot["amount"]))
        if retry_outcome.success_probability >= self.SUCCESS_THRESHOLD:
            recovered = amount
            outcome = "baseline_recovered"
        else:
            recovered = Decimal("0")
            outcome = "baseline_no_recovery"

        return {
            "payment_id": resolved_id,
            "strategy": "retry_now",
            "recovered_amount": recovered,
            "outcome": outcome,
            "eligible": True,
            "success_probability": float(retry_outcome.success_probability),
        }


class BatchEvaluator:
    """Batch evaluation system that compares PayFix recovery against a baseline.

    The evaluator is fully READ-ONLY against the database. Every payment is
    copied into an in-memory snapshot at the start of the run and the entire
    PayFix pipeline (diagnosis -> eligibility -> simulation -> ranking ->
    guardrails -> simulated executor) is executed against that snapshot.

    No `RecoveryService.recover_payment()` is invoked from this class, and no
    repository write/audit/recovery-attempt side effect ever fires.
    """

    def __init__(self, repository: PayFixRepository) -> None:
        self.repository = repository
        self.baseline = EvaluationBaseline(repository)
        # Pipeline components used purely in-memory; they share the same
        # repository reference for type compatibility, but this evaluator never
        # lets them touch it.
        self._diagnosis_service = None
        self._optimizer = None
        self._eligibility_engine = None
        self._guardrails = None
        self._executor = None

    def _pipeline_components(self):
        """Lazily construct the read-only PayFix pipeline components."""
        if self._diagnosis_service is None:
            from .diagnosis_service import DiagnosisService
            from .eligibility import EligibilityEngine
            from .guardrails import RecoveryGuardrails
            from .optimizer import ExpectedRecoveryOptimizer
            from .simulated_executor import SimulatedRecoveryExecutor

            self._diagnosis_service = DiagnosisService(self.repository)
            self._eligibility_engine = EligibilityEngine()
            self._optimizer = ExpectedRecoveryOptimizer(
                self.repository,
                diagnosis_service=self._diagnosis_service,
                eligibility_engine=self._eligibility_engine,
            )
            self._guardrails = RecoveryGuardrails()
            self._executor = SimulatedRecoveryExecutor()
        return (
            self._diagnosis_service,
            self._optimizer,
            self._eligibility_engine,
            self._guardrails,
            self._executor,
        )

    def run_evaluation(
        self,
        batch_size: int = 100,
        payment_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        """Run a deterministic, READ-ONLY batch evaluation.

        Repeated calls on the same underlying dataset produce identical metrics
        and never mutate payment, recovery_attempt, or audit_log tables.
        """
        if batch_size < 0:
            batch_size = 0

        payment_snapshots = self._get_payment_snapshots(batch_size, payment_ids)

        metrics = self._initialize_metrics()

        for snapshot in payment_snapshots:
            self._evaluate_single_snapshot(snapshot, metrics)

        results = self._calculate_final_metrics(metrics, len(payment_snapshots))

        return results

    def _get_payment_snapshots(
        self,
        batch_size: int,
        payment_ids: List[str] | None,
    ) -> List[Dict[str, Any]]:
        """Select candidate payments and copy them into immutable snapshots.

        Each snapshot is a brand-new dict decoupled from the live row, so any
        mutation downstream cannot affect stored state.
        """
        if payment_ids:
            snapshots: List[Dict[str, Any]] = []
            for pid in payment_ids:
                row = self.repository.get_payment(pid)
                if row is None:
                    continue
                snapshot = self._freeze_snapshot(row)
                if not self._is_already_successfully_recovered(snapshot):
                    snapshots.append(snapshot)
            return snapshots[:batch_size]

        all_payments = self.repository.list_payments()
        failed_payments = [
            p for p in all_payments if p.get("payment_status") == "failed"
        ]
        unrecovered = [
            p for p in failed_payments
            if not self._is_already_successfully_recovered(p)
        ]
        snapshots = [self._freeze_snapshot(p) for p in unrecovered]
        return snapshots[:batch_size]

    @staticmethod
    def _freeze_snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
        """Return an independent dict copy of a repository row.

        Decoupled from the live row so downstream simulation cannot mutate
        stored state.
        """
        return {str(key): value for key, value in row.items()}

    @staticmethod
    def _is_already_successfully_recovered(payment: Mapping[str, Any]) -> bool:
        """Skip payments that already had a successful simulated recovery."""
        recovery_status = payment.get("recovery_status", "not_started")
        recovered_amount = Decimal(str(payment.get("recovered_amount", 0)))
        return (
            recovery_status == "simulated_recovered"
            and recovered_amount > 0
        )

    def _initialize_metrics(self) -> Dict[str, Any]:
        """Initialize metrics structure."""
        return {
            "payments_evaluated": 0,
            "revenue_at_risk": Decimal("0"),
            "baseline_recovered": Decimal("0"),
            "payfix_recovered": Decimal("0"),
            "successful_recoveries": 0,
            "human_escalations": 0,
            "stopped_cases": 0,
            "blocked_actions": 0,
            "baseline_recovery_rate": Decimal("0"),
            "payfix_recovery_rate": Decimal("0"),
            "recovered_revenue_uplift": Decimal("0"),
            "recovered_revenue_uplift_percentage": Decimal("0"),
            "strategy_metrics": defaultdict(lambda: {
                "count": 0,
                "successful_recoveries": 0,
                "recovered_amount": Decimal("0"),
            }),
        }

    def _evaluate_single_snapshot(
        self,
        snapshot: Dict[str, Any],
        metrics: Dict[str, Any],
    ) -> None:
        """Evaluate a single snapshot against both baseline and PayFix.

        Both legs operate purely against the in-memory snapshot. No payment
        row, audit log, or recovery_attempt row is ever written.
        """
        payment_id = str(snapshot.get("payment_id", ""))
        amount = Decimal(str(snapshot["amount"]))
        metrics["payments_evaluated"] += 1
        metrics["revenue_at_risk"] += amount

        # Baseline: pass the snapshot directly (no repository read inside).
        baseline_result = self.baseline.evaluate_snapshot(snapshot, payment_id=payment_id)
        metrics["baseline_recovered"] += baseline_result["recovered_amount"]

        # PayFix: run the full pipeline against the snapshot.
        payfix_result = self._evaluate_payfix_from_snapshot(snapshot, payment_id)

        metrics["payfix_recovered"] += payfix_result["recovered_amount"]
        metrics["successful_recoveries"] += payfix_result["successful_recoveries"]
        metrics["human_escalations"] += payfix_result["human_escalations"]
        metrics["stopped_cases"] += payfix_result["stopped_cases"]
        metrics["blocked_actions"] += payfix_result["blocked_actions"]

        for strategy, strategy_metrics in payfix_result["strategy_metrics"].items():
            metrics["strategy_metrics"][strategy]["count"] += 1
            metrics["strategy_metrics"][strategy]["successful_recoveries"] += (
                strategy_metrics["successful_recoveries"]
            )
            metrics["strategy_metrics"][strategy]["recovered_amount"] += (
                strategy_metrics["recovered_amount"]
            )

    def _evaluate_payfix_from_snapshot(
        self,
        snapshot: Dict[str, Any],
        payment_id: str,
    ) -> Dict[str, Any]:
        """Run the PayFix pipeline purely on the snapshot. No DB writes."""
        (
            _diagnosis_service,
            optimizer,
            _eligibility_engine,
            guardrails,
            executor,
        ) = self._pipeline_components()

        try:
            optimization = optimizer.optimize_from_snapshot(snapshot, payment_id=payment_id)
            eligibility = list(_eligibility_engine.evaluate(snapshot))
            outcome, guardrail, _reason = self._authorize_snapshot(
                snapshot, optimization, eligibility, guardrails
            )
            execution = executor.execute(snapshot, outcome, guardrail)
        except Exception as e:  # noqa: BLE001
            return self._create_failed_result(payment_id, str(e))

        # Record metrics under the strategy that was actually authorized and
        # executed (the fallback chosen by guardrails), not the optimizer's
        # top recommendation. When guardrails block every ranked strategy,
        # the executed strategy may be the safe fallback (human_escalation /
        # stop) rather than optimization.selected_strategy.
        strategy = outcome.strategy

        result: Dict[str, Any] = {
            "recovered_amount": execution.recovered_amount,
            "successful_recoveries": 0,
            "human_escalations": 0,
            "stopped_cases": 0,
            "blocked_actions": 0,
            "strategy_metrics": defaultdict(lambda: {
                "count": 0,
                "successful_recoveries": 0,
                "recovered_amount": Decimal("0"),
            }),
        }

        if execution.simulated_outcome == "simulated_recovered":
            result["successful_recoveries"] = 1
        elif execution.simulated_outcome == "simulated_human_review_queued":
            result["human_escalations"] = 1
        elif execution.simulated_outcome == "no_action":
            result["stopped_cases"] = 1
        elif execution.simulated_outcome == "blocked":
            result["blocked_actions"] = 1

        if execution.execution_allowed:
            result["strategy_metrics"][strategy]["count"] = 1
            if execution.simulated_outcome == "simulated_recovered":
                result["strategy_metrics"][strategy]["successful_recoveries"] = 1
            result["strategy_metrics"][strategy]["recovered_amount"] += (
                execution.recovered_amount
            )

        return result

    @staticmethod
    def _authorize_snapshot(
        snapshot: Mapping[str, Any],
        optimization,
        eligibility,
        guardrails,
    ):
        """Mirror RecoveryService._authorize using only the snapshot."""
        from .simulation_models import SimulatedStrategyOutcome

        for outcome in optimization.ranked_strategies:
            guardrail = guardrails.evaluate(snapshot, outcome.strategy, eligibility)
            if not guardrail.allowed:
                continue
            reason = optimization.selection_reason
            if outcome.strategy != optimization.selected_strategy:
                reason = (
                    f"Optimizer recommended {optimization.selected_strategy}, but "
                    f"guardrails blocked it. {outcome.strategy} is the highest-ranked "
                    f"strategy authorized by guardrails."
                )
            return outcome, guardrail, reason

        eligible = {item.strategy: item.eligible for item in eligibility}
        fallback = "human_escalation" if eligible.get("human_escalation") else "stop"
        guardrail = guardrails.evaluate(snapshot, fallback, eligibility)
        fallback_outcome: SimulatedStrategyOutcome
        for outcome in (*optimization.ranked_strategies, *optimization.strategies_evaluated):
            if outcome.strategy == fallback:
                fallback_outcome = outcome
                break
        else:
            fallback_outcome = SimulatedStrategyOutcome(
                str(snapshot.get("payment_id", "")),
                fallback,
                True,
                Decimal("0"),
                Decimal("0"),
                "none",
                "no further action",
                "Safe fallback strategy with no synthetic recovery estimate.",
                ["Guardrail fallback; no automated recovery was authorized."],
            )
        return (
            fallback_outcome,
            guardrail,
            (
                f"Optimizer recommended {optimization.selected_strategy}, but guardrails "
                f"blocked every ranked strategy. {fallback} was selected as the safe "
                f"fallback."
            ),
        )

    def _create_failed_result(
        self,
        payment_id: str,
        error: str,
    ) -> Dict[str, Any]:
        """Create a result for a failed PayFix evaluation."""
        return {
            "recovered_amount": Decimal("0"),
            "successful_recoveries": 0,
            "human_escalations": 0,
            "stopped_cases": 0,
            "blocked_actions": 1,
            "strategy_metrics": {},
            "error": error,
        }

    def _calculate_final_metrics(
        self,
        metrics: Dict[str, Any],
        payments_evaluated: int,
    ) -> Dict[str, Any]:
        """Calculate final metrics from collected data."""
        if metrics["revenue_at_risk"] > 0:
            metrics["baseline_recovery_rate"] = (
                metrics["baseline_recovered"]
                / metrics["revenue_at_risk"]
            )
            metrics["payfix_recovery_rate"] = (
                metrics["payfix_recovered"] / metrics["revenue_at_risk"]
            )

        metrics["recovered_revenue_uplift"] = (
            metrics["payfix_recovered"] - metrics["baseline_recovered"]
        )

        if metrics["baseline_recovered"] > 0:
            metrics["recovered_revenue_uplift_percentage"] = (
                (metrics["recovered_revenue_uplift"] / metrics["baseline_recovered"])
                * 100
            )

        result = {
            "payments_evaluated": payments_evaluated,
            "revenue_at_risk": float(metrics["revenue_at_risk"]),
            "baseline_recovered": float(metrics["baseline_recovered"]),
            "payfix_recovered": float(metrics["payfix_recovered"]),
            "successful_recoveries": metrics["successful_recoveries"],
            "human_escalations": metrics["human_escalations"],
            "stopped_cases": metrics["stopped_cases"],
            "blocked_actions": metrics["blocked_actions"],
            "baseline_recovery_rate": float(metrics["baseline_recovery_rate"]),
            "payfix_recovery_rate": float(metrics["payfix_recovery_rate"]),
            "recovered_revenue_uplift": float(metrics["recovered_revenue_uplift"]),
            "recovered_revenue_uplift_percentage": float(
                metrics["recovered_revenue_uplift_percentage"]
            ),
            "strategy_metrics": {
                strategy: {
                    "count": data["count"],
                    "successful_recoveries": data["successful_recoveries"],
                    "recovered_amount": float(data["recovered_amount"]),
                }
                for strategy, data in metrics["strategy_metrics"].items()
            },
        }

        return result


# Import here to avoid circular imports - only used by EvaluationBaseline
class PaymentNotFoundError(Exception):
    pass
