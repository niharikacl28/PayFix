"""Service boundary that combines diagnosis with authoritative eligibility results."""

from __future__ import annotations

from typing import Any, Mapping

from .diagnosis_models import DiagnosisResult
from .diagnosis_providers import DiagnosisProvider, get_diagnosis_provider
from .eligibility import EligibilityEngine
from .repositories import PayFixRepository


class PaymentNotFoundError(LookupError):
    """Raised when a diagnosis is requested for an absent payment."""


class DiagnosisService:
    """Load facts, diagnose them, then append deterministic eligible strategies."""

    def __init__(self, repository: PayFixRepository, provider: DiagnosisProvider | None = None, eligibility_engine: EligibilityEngine | None = None) -> None:
        self.repository = repository
        self.provider = provider or get_diagnosis_provider()
        self.eligibility_engine = eligibility_engine or EligibilityEngine()

    def diagnose_payment(self, payment_id: str) -> DiagnosisResult:
        payment = self.repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment '{payment_id}' was not found.")
        return self.diagnose_from_snapshot(payment, payment_id=payment_id)

    def diagnose_from_snapshot(self, snapshot: Mapping[str, Any], payment_id: str | None = None) -> DiagnosisResult:
        """Diagnose from an in-memory snapshot of payment facts.

        Does NOT touch the repository. Used by the read-only BatchEvaluator to
        evaluate payments deterministically without mutating stored state.
        """
        resolved_id = payment_id or str(snapshot.get("payment_id", ""))
        draft = self.provider.diagnose(snapshot)
        eligible_strategy_names = [
            item.strategy
            for item in self.eligibility_engine.evaluate(snapshot)
            if item.eligible
        ]
        return DiagnosisResult(
            payment_id=resolved_id,
            eligible_strategy_names=eligible_strategy_names,
            **draft.__dict__,
        )
