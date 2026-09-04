"""Rank synthetic outcomes without making an irreversible recovery decision."""

from __future__ import annotations

from typing import Any, Mapping

from .diagnosis_service import DiagnosisService, PaymentNotFoundError
from .eligibility import EligibilityEngine
from .repositories import PayFixRepository
from .simulation_models import OptimizationResult
from .strategy_simulator import SIMULATION_VERSION, StrategySimulator


class ExpectedRecoveryOptimizer:
    """Rank eligible strategies by their synthetic expected recovered amount."""

    def __init__(self, repository: PayFixRepository, diagnosis_service: DiagnosisService | None = None, eligibility_engine: EligibilityEngine | None = None, simulator: StrategySimulator | None = None) -> None:
        self.repository = repository
        self.diagnosis_service = diagnosis_service or DiagnosisService(repository)
        self.eligibility_engine = eligibility_engine or EligibilityEngine()
        self.simulator = simulator or StrategySimulator()

    def optimize_payment(self, payment_id: str) -> OptimizationResult:
        payment = self.repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment '{payment_id}' was not found.")
        return self.optimize_from_snapshot(payment, payment_id=payment_id)

    def optimize_from_snapshot(self, snapshot: Mapping[str, Any], payment_id: str | None = None) -> OptimizationResult:
        """Rank strategies against an in-memory snapshot of payment facts.

        Does NOT touch the repository. Used by the read-only BatchEvaluator to
        evaluate payments deterministically without mutating stored state.
        """
        resolved_id = payment_id or str(snapshot.get("payment_id", ""))
        diagnosis = self.diagnosis_service.diagnose_from_snapshot(snapshot, payment_id=resolved_id)
        outcomes = self.simulator.simulate(snapshot, diagnosis, self.eligibility_engine.evaluate(snapshot))
        ranked = sorted(outcomes, key=lambda item: (-item.expected_recovered_amount, item.strategy))
        selected = ranked[0]
        return OptimizationResult(
            resolved_id,
            outcomes,
            ranked,
            selected.strategy,
            selected.expected_recovered_amount,
            f"{selected.strategy} has the highest synthetic expected recovered amount among eligible strategies.",
            SIMULATION_VERSION,
        )
