"""Strict, serializable models for payment-failure diagnosis."""

from dataclasses import asdict, dataclass
from enum import Enum


class FailureCategory(str, Enum):
    TEMPORARY_FAILURE = "temporary_failure"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_PAYMENT_METHOD = "expired_payment_method"
    PERMANENT_DECLINE = "permanent_decline"
    SUSPECTED_FRAUD = "suspected_fraud"
    RECURRING_MANDATE_FAILURE = "recurring_mandate_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiagnosisDraft:
    """Provider output before deterministic strategy eligibility is attached."""

    failure_category: FailureCategory
    likely_cause: str
    confidence: float
    explanation: str
    retryability_assessment: str
    risk_observation: str
    timing_observation: str
    customer_context_observation: str

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("Diagnosis confidence must be between 0 and 1.")


@dataclass(frozen=True)
class DiagnosisResult(DiagnosisDraft):
    """Diagnosis response with strategy names supplied by the eligibility engine."""

    payment_id: str
    eligible_strategy_names: list[str]

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible response data without provider internals."""
        result = asdict(self)
        result["failure_category"] = self.failure_category.value
        return result
