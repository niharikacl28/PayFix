"""Read-only service that assembles a payment's recovery decision snapshot.

Reuses the existing diagnosis and optimizer services, then augments with the
already-persisted recovery_attempt, audit_log, and (when available) the
immutable decision_snapshots row. It NEVER calls RecoveryService, NEVER runs
the executor, and NEVER writes to the repository.

For an already-executed recovery the response is reconstructed from the
original decision snapshot recorded at execution time, so the detail page
reflects the *original* decision rather than a replay against mutated state.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

from .diagnosis_models import DiagnosisResult, FailureCategory
from .diagnosis_service import DiagnosisService, PaymentNotFoundError
from .eligibility import EligibilityEngine
from .guardrails import GuardrailResult
from .optimizer import ExpectedRecoveryOptimizer
from .recovery_models import RecoveryDecision, SimulatedExecutionResult
from .repositories import PayFixRepository
from .simulation_models import OptimizationResult, SimulatedStrategyOutcome
from .strategy_simulator import SIMULATION_VERSION


def _deserialize_diagnosis(payment_id: str, payload: Mapping[str, object]) -> DiagnosisResult:
    raw_confidence = payload.get("confidence")
    if raw_confidence is None:
        confidence = 0.0
    else:
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0
    # DiagnosisResult's fields are declared in this order:
    #   (DiagnosisDraft) failure_category, likely_cause, confidence,
    #   explanation, retryability_assessment, risk_observation,
    #   timing_observation, customer_context_observation,
    #   (DiagnosisResult) payment_id, eligible_strategy_names
    return DiagnosisResult(
        FailureCategory(str(payload.get("failure_category") or "unknown")),
        str(payload.get("likely_cause") or ""),
        confidence,
        str(payload.get("explanation") or ""),
        str(payload.get("retryability_assessment") or ""),
        str(payload.get("risk_observation") or ""),
        str(payload.get("timing_observation") or ""),
        str(payload.get("customer_context_observation") or ""),
        payment_id,
        list(payload.get("eligible_strategy_names") or []),
    )


def _deserialize_outcome(payment_id: str, payload: Mapping[str, object]) -> SimulatedStrategyOutcome:
    return SimulatedStrategyOutcome(
        payment_id,
        str(payload.get("strategy") or ""),
        bool(payload.get("eligible", True)),
        Decimal(str(payload.get("success_probability") or 0)),
        Decimal(str(payload.get("expected_recovered_amount") or 0)),
        str(payload.get("estimated_customer_friction") or "none"),
        str(payload.get("estimated_time_to_recovery") or "no further action"),
        str(payload.get("rationale") or ""),
        list(payload.get("assumptions") or []),
    )


def _deserialize_optimization(
    payment_id: str, payload: Mapping[str, object]
) -> OptimizationResult:
    evaluated = [
        _deserialize_outcome(payment_id, item)
        for item in (payload.get("strategies_evaluated") or [])
    ]
    ranked = [
        _deserialize_outcome(payment_id, item)
        for item in (payload.get("ranked_strategies") or [])
    ]
    if not ranked and evaluated:
        ranked = list(evaluated)
    if not evaluated and ranked:
        evaluated = list(ranked)
    return OptimizationResult(
        payment_id,
        evaluated,
        ranked,
        str(payload.get("selected_strategy") or (ranked[0].strategy if ranked else "")),
        Decimal(str(payload.get("expected_recovered_amount") or 0)),
        str(payload.get("selection_reason") or ""),
        str(payload.get("simulation_version") or SIMULATION_VERSION),
    )


def _deserialize_guardrail(payload: Mapping[str, object]) -> GuardrailResult:
    return GuardrailResult(
        str(payload.get("strategy") or ""),
        bool(payload.get("allowed", False)),
        str(payload.get("reason") or ""),
        list(payload.get("checks") or []),
    )


def _deserialize_execution(payment_id: str, payload: Mapping[str, object]) -> SimulatedExecutionResult:
    return SimulatedExecutionResult(
        payment_id,
        str(payload.get("selected_strategy") or ""),
        bool(payload.get("execution_allowed", False)),
        _deserialize_guardrail(payload.get("guardrail_result") or {}),
        str(payload.get("simulated_outcome") or "no_action"),
        Decimal(str(payload.get("recovered_amount") or 0)),
        str(payload.get("reason") or ""),
        str(payload.get("timestamp") or ""),
    )


class DecisionService:
    """Build a non-mutating snapshot of a payment's existing recovery decision."""

    def __init__(
        self,
        repository: PayFixRepository,
        diagnosis_service: DiagnosisService | None = None,
        optimizer: ExpectedRecoveryOptimizer | None = None,
        eligibility_engine: EligibilityEngine | None = None,
    ) -> None:
        self.repository = repository
        self.diagnosis_service = diagnosis_service or DiagnosisService(repository)
        self.optimizer = optimizer or ExpectedRecoveryOptimizer(
            repository, self.diagnosis_service
        )
        self.eligibility_engine = eligibility_engine or EligibilityEngine()

    def get_decision(self, payment_id: str) -> RecoveryDecision:
        """Return a read-only snapshot of the existing decision for ``payment_id``.

        If a previously-executed recovery has persisted a decision snapshot, the
        response is reconstructed verbatim from that snapshot (the *original*
        decision). Otherwise the response is assembled from the persisted
        recovery_attempt and audit_log rows, with empty diagnosis / strategy
        lists to make it clear that the original decision context is not
        preserved. Nothing is written to the database.
        """
        if self.repository.get_payment(payment_id) is None:
            raise PaymentNotFoundError(f"Payment '{payment_id}' was not found.")

        snapshot = self.repository.get_decision_snapshot(payment_id)
        if snapshot is not None:
            return self._from_snapshot(payment_id, snapshot)

        return self._from_attempt_only(payment_id)

    def _from_snapshot(self, payment_id: str, snapshot: Mapping[str, Any]) -> RecoveryDecision:
        diagnosis = _deserialize_diagnosis(payment_id, json.loads(snapshot["diagnosis_json"]))
        optimization = _deserialize_optimization(
            payment_id, json.loads(snapshot["optimization_json"])
        )
        guardrail = _deserialize_guardrail(json.loads(snapshot["guardrail_json"]))
        execution = _deserialize_execution(payment_id, json.loads(snapshot["execution_json"]))
        # Execution's selected_strategy is the executor's view; the snapshot's
        # top-level selected_strategy is the source of truth (it matches the
        # optimizer/guardrail authorization that ran).
        execution = SimulatedExecutionResult(
            payment_id,
            snapshot["selected_strategy"],
            bool(execution.execution_allowed),
            guardrail,
            execution.simulated_outcome,
            execution.recovered_amount,
            execution.reason,
            execution.timestamp,
        )
        return RecoveryDecision(
            payment_id,
            diagnosis,
            optimization,
            snapshot["selected_strategy"],
            Decimal(str(snapshot["expected_recovered_amount"])),
            snapshot["selection_reason"],
            guardrail,
            execution,
        )

    def _from_attempt_only(self, payment_id: str) -> RecoveryDecision:
        """Legacy / pre-snapshot path: be honest about what cannot be reconstructed."""
        attempts = self.repository.list_recovery_attempts(payment_id)
        audits = self.repository.list_audit_logs(payment_id)
        attempt = attempts[-1] if attempts else None
        audit = audits[-1] if audits else None

        if attempt is None and audit is None:
            # No recovery has been performed. Run diagnosis + optimization
            # fresh against the live row (matching /diagnose + /optimize) and
            # surface an explicit "no execution yet" execution frame.
            return self._no_execution_yet(payment_id)

        # Strategy + outcome + recovered amount + reason + timestamp are
        # reconstructible from the attempt row. Diagnosis explanation and
        # selection rationale are reconstructible from the audit row.
        # Everything else (eligible strategies, ranked outcomes, success
        # probabilities) was never persisted and would be a fabrication.
        # We surface empty lists rather than recompute against mutated state.
        strategy = str((attempt or {}).get("strategy") or (audit or {}).get("selected_action") or "")
        recovered = Decimal(str((attempt or {}).get("recovered_amount") or 0))
        outcome = str((attempt or {}).get("result") or (audit or {}).get("execution_result") or "no_action")
        reason = str((attempt or {}).get("reason") or "")
        timestamp = str(
            (attempt or {}).get("completed_at")
            or (attempt or {}).get("created_at")
            or (audit or {}).get("created_at")
            or ""
        )
        diagnosis_explanation = str((audit or {}).get("diagnosis") or "")
        diagnosis_rationale = str((audit or {}).get("action_rationale") or "")
        guardrail_allowed = bool((audit or {}).get("guardrails_passed", True)) if audit else True
        guardrail = GuardrailResult(
            strategy,
            guardrail_allowed,
            "Original guardrail context was not persisted with this legacy attempt.",
            ["Original guardrail context was not preserved; only the final decision is shown."],
        )
        # Build a "minimal" diagnosis and optimization that only contain the
        # information we can truthfully reconstruct. The UI already handles
        # empty eligible_strategy_names and empty ranked_strategies gracefully.
        empty_outcome = SimulatedStrategyOutcome(
            payment_id,
            strategy,
            True,
            Decimal("0"),
            recovered,
            "unknown",
            "unknown",
            "Original simulated outcome data is not preserved for this legacy payment.",
            ["The decision was executed before the immutable snapshot was added."],
        )
        optimization = OptimizationResult(
            payment_id,
            [empty_outcome],
            [empty_outcome],
            strategy,
            recovered,
            (
                f"{diagnosis_rationale} Original strategy outcome details were not "
                "preserved for this legacy attempt."
                if diagnosis_rationale
                else "Original strategy outcome details were not preserved for this legacy attempt."
            ),
            SIMULATION_VERSION,
        )
        diagnosis = DiagnosisResult(
            FailureCategory("unknown"),
            "Original failure cause was not preserved with this legacy attempt.",
            0.0,
            diagnosis_explanation or "Original diagnosis context was not preserved for this legacy attempt.",
            "Original retryability assessment was not preserved for this legacy attempt.",
            "Original risk observation was not preserved for this legacy attempt.",
            "Original timing observation was not preserved for this legacy attempt.",
            "Original customer context was not preserved for this legacy attempt.",
            payment_id,
            [],  # eligible_strategy_names intentionally empty — not reconstructible
        )
        execution = SimulatedExecutionResult(
            payment_id,
            strategy,
            guardrail_allowed,
            guardrail,
            outcome,
            recovered,
            reason,
            timestamp,
        )
        return RecoveryDecision(
            payment_id,
            diagnosis,
            optimization,
            strategy,
            recovered,
            diagnosis_rationale or "Original selection reasoning was not preserved for this legacy attempt.",
            guardrail,
            execution,
        )

    def _no_execution_yet(self, payment_id: str) -> RecoveryDecision:
        diagnosis = self.diagnosis_service.diagnose_payment(payment_id)
        optimization = self.optimizer.optimize_payment(payment_id)
        guardrail = GuardrailResult(
            optimization.selected_strategy,
            True,
            "Guardrails have not yet evaluated this strategy.",
            ["Strategy eligibility was evaluated by the deterministic backend."],
        )
        execution = SimulatedExecutionResult(
            payment_id,
            optimization.selected_strategy,
            True,
            guardrail,
            "no_action",
            Decimal("0"),
            "No simulated execution has been recorded for this payment yet.",
            "",
        )
        return RecoveryDecision(
            payment_id,
            diagnosis,
            optimization,
            optimization.selected_strategy,
            optimization.expected_recovered_amount,
            optimization.selection_reason,
            guardrail,
            execution,
        )
