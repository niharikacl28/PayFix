"""Structured records for bounded recovery decisions and simulated execution."""

from dataclasses import asdict, dataclass
from decimal import Decimal

from .diagnosis_models import DiagnosisResult
from .guardrails import GuardrailResult
from .simulation_models import OptimizationResult


@dataclass(frozen=True)
class SimulatedExecutionResult:
    payment_id: str
    selected_strategy: str
    execution_allowed: bool
    guardrail_result: GuardrailResult
    simulated_outcome: str
    recovered_amount: Decimal
    reason: str
    timestamp: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["recovered_amount"] = float(self.recovered_amount)
        return result


@dataclass(frozen=True)
class RecoveryDecision:
    payment_id: str
    diagnosis: DiagnosisResult
    optimization: OptimizationResult
    selected_strategy: str
    expected_recovered_amount: Decimal
    selection_reason: str
    guardrail_result: GuardrailResult
    execution: SimulatedExecutionResult

    def to_dict(self) -> dict[str, object]:
        return {
            "payment_id": self.payment_id,
            "diagnosis": self.diagnosis.to_dict(),
            "optimization": self.optimization.to_dict(),
            "selected_strategy": self.selected_strategy,
            "expected_recovered_amount": float(self.expected_recovered_amount),
            "selection_reason": self.selection_reason,
            "guardrail_result": self.guardrail_result.to_dict(),
            "execution": self.execution.to_dict(),
        }
