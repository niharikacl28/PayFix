"""Orchestrate diagnosis, optimization, guardrails, and simulated recovery."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from .diagnosis_models import DiagnosisResult
from .diagnosis_service import DiagnosisService, PaymentNotFoundError
from .eligibility import EligibilityEngine, StrategyEligibility
from .guardrails import CONTACT_STRATEGIES, RETRY_STRATEGIES, GuardrailResult, RecoveryGuardrails
from .models import AuditLog, RecoveryAttempt, utc_now
from .optimizer import ExpectedRecoveryOptimizer
from .recovery_models import RecoveryDecision, SimulatedExecutionResult
from .repositories import PayFixRepository
from .simulated_executor import SimulatedRecoveryExecutor
from .simulation_models import OptimizationResult, SimulatedStrategyOutcome


COMPLETED_RECOVERY_STATUS = "simulated_recovered"
_OUTCOME_STATUS = {
    "simulated_recovered": COMPLETED_RECOVERY_STATUS,
    "simulated_no_recovery": "simulated_no_recovery",
    "no_action": "stopped",
    "simulated_human_review_queued": "human_review",
    "blocked": "blocked",
}


class RecoveryService:
    """Authorize and persist a single synthetic recovery decision per payment."""

    def __init__(
        self,
        repository: PayFixRepository,
        diagnosis_service: DiagnosisService | None = None,
        optimizer: ExpectedRecoveryOptimizer | None = None,
        eligibility_engine: EligibilityEngine | None = None,
        guardrails: RecoveryGuardrails | None = None,
        executor: SimulatedRecoveryExecutor | None = None,
    ) -> None:
        self.repository = repository
        self.diagnosis_service = diagnosis_service or DiagnosisService(repository)
        self.optimizer = optimizer or ExpectedRecoveryOptimizer(repository, self.diagnosis_service)
        self.eligibility_engine = eligibility_engine or EligibilityEngine()
        self.guardrails = guardrails or RecoveryGuardrails()
        self.executor = executor or SimulatedRecoveryExecutor()

    def recover_payment(self, payment_id: str) -> RecoveryDecision:
        payment = self.repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment '{payment_id}' was not found.")
        if self._already_recovered(payment):
            return self._replay_completed_recovery(payment)

        diagnosis = self.diagnosis_service.diagnose_payment(payment_id)
        optimization = self.optimizer.optimize_payment(payment_id)
        eligibility = self.eligibility_engine.evaluate(payment)
        outcome, guardrail, selection_reason = self._authorize(payment, optimization, eligibility)
        execution = self.executor.execute(payment, outcome, guardrail)
        self._persist(payment, diagnosis, optimization, outcome, guardrail, execution, selection_reason)
        return RecoveryDecision(
            payment_id,
            diagnosis,
            optimization,
            outcome.strategy,
            outcome.expected_recovered_amount,
            selection_reason,
            guardrail,
            execution,
        )

    @staticmethod
    def _already_recovered(payment: Mapping[str, Any]) -> bool:
        if payment.get("recovery_status") == COMPLETED_RECOVERY_STATUS:
            return True
        return Decimal(str(payment.get("recovered_amount") or 0)) > 0

    def _replay_completed_recovery(self, payment: Mapping[str, Any]) -> RecoveryDecision:
        payment_id = payment["payment_id"]
        diagnosis = self.diagnosis_service.diagnose_payment(payment_id)
        optimization = self.optimizer.optimize_payment(payment_id)
        attempts = self.repository.list_recovery_attempts(payment_id)
        attempt = attempts[-1] if attempts else None
        strategy = str(attempt["strategy"] if attempt else optimization.selected_strategy)
        recovered = Decimal(str(payment.get("recovered_amount") or 0))
        timestamp = str((attempt or {}).get("completed_at") or (attempt or {}).get("created_at") or utc_now())
        reason = str((attempt or {}).get("reason") or "Simulated recovery was already completed for this payment.")
        guardrail = GuardrailResult(
            strategy,
            True,
            "Simulated recovery was already completed for this payment.",
            ["Idempotent replay of a completed simulated recovery; no additional action was taken."],
        )
        execution = SimulatedExecutionResult(
            payment_id, strategy, True, guardrail, "simulated_recovered", recovered, reason, timestamp
        )
        expected = recovered
        for item in optimization.ranked_strategies:
            if item.strategy == strategy:
                expected = item.expected_recovered_amount
                break
        return RecoveryDecision(
            payment_id,
            diagnosis,
            optimization,
            strategy,
            expected,
            "Simulated recovery was already completed for this payment; no additional action was taken.",
            guardrail,
            execution,
        )

    def _authorize(
        self,
        payment: Mapping[str, Any],
        optimization: OptimizationResult,
        eligibility: Sequence[StrategyEligibility],
    ) -> tuple[SimulatedStrategyOutcome, GuardrailResult, str]:
        for outcome in optimization.ranked_strategies:
            guardrail = self.guardrails.evaluate(payment, outcome.strategy, eligibility)
            if not guardrail.allowed:
                continue
            reason = optimization.selection_reason
            if outcome.strategy != optimization.selected_strategy:
                reason = (
                    f"Optimizer recommended {optimization.selected_strategy}, but guardrails blocked it. "
                    f"{outcome.strategy} is the highest-ranked strategy authorized by guardrails."
                )
            return outcome, guardrail, reason

        eligible = {item.strategy: item.eligible for item in eligibility}
        fallback = "human_escalation" if eligible.get("human_escalation") else "stop"
        guardrail = self.guardrails.evaluate(payment, fallback, eligibility)
        return (
            self._outcome_named(optimization, fallback, payment),
            guardrail,
            (
                f"Optimizer recommended {optimization.selected_strategy}, but guardrails blocked every ranked strategy. "
                f"{fallback} was selected as the safe fallback."
            ),
        )

    @staticmethod
    def _outcome_named(optimization: OptimizationResult, strategy: str, payment: Mapping[str, Any]) -> SimulatedStrategyOutcome:
        for outcome in (*optimization.ranked_strategies, *optimization.strategies_evaluated):
            if outcome.strategy == strategy:
                return outcome
        return SimulatedStrategyOutcome(
            payment["payment_id"],
            strategy,
            True,
            Decimal("0"),
            Decimal("0"),
            "none",
            "no further action",
            "Safe fallback strategy with no synthetic recovery estimate.",
            ["Guardrail fallback; no automated recovery was authorized."],
        )

    def _persist(
        self,
        payment: Mapping[str, Any],
        diagnosis: DiagnosisResult,
        optimization: OptimizationResult,
        outcome: SimulatedStrategyOutcome,
        guardrail: GuardrailResult,
        execution: SimulatedExecutionResult,
        selection_reason: str,
    ) -> None:
        payment_id = payment["payment_id"]
        self.repository.create_recovery_attempt(
            RecoveryAttempt(
                payment_id,
                outcome.strategy,
                status="completed",
                result=execution.simulated_outcome,
                recovered_amount=execution.recovered_amount,
                reason=execution.reason,
                completed_at=execution.timestamp,
            )
        )
        updates: dict[str, Any] = {
            "recovery_status": _OUTCOME_STATUS.get(execution.simulated_outcome, "attempted"),
            "recovered_amount": execution.recovered_amount,
            "updated_at": execution.timestamp,
        }
        if execution.simulated_outcome == "simulated_recovered":
            updates["payment_status"] = "succeeded"
            updates["last_successful_payment_at"] = execution.timestamp
        if execution.execution_allowed and execution.simulated_outcome not in {"blocked", "no_action", "simulated_human_review_queued"}:
            if outcome.strategy in RETRY_STRATEGIES:
                updates["retry_count"] = int(payment.get("retry_count") or 0) + 1
            if outcome.strategy in CONTACT_STRATEGIES or outcome.strategy == "alternate_payment_method":
                updates["customer_contact_count"] = int(payment.get("customer_contact_count") or 0) + 1
        self.repository.update_payment(payment_id, **updates)
        self.repository.create_audit_log(
            AuditLog(
                payment_id,
                "recovery_executed",
                (
                    f"Simulated recovery executed strategy {outcome.strategy} "
                    f"with outcome {execution.simulated_outcome}."
                ),
                diagnosis=diagnosis.explanation,
                strategies_considered=[item.strategy for item in optimization.ranked_strategies],
                selected_action=outcome.strategy,
                action_rationale=selection_reason,
                guardrails_passed=guardrail.allowed,
                execution_result=execution.simulated_outcome,
                recovered_amount=execution.recovered_amount,
                created_at=execution.timestamp,
            )
        )
