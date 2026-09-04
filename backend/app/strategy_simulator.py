"""Transparent synthetic estimates for strategies already allowed by eligibility."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from .diagnosis_models import DiagnosisResult, FailureCategory
from .eligibility import StrategyEligibility
from .simulation_models import SimulatedStrategyOutcome


SIMULATION_VERSION = "synthetic-v1"
_BASE_PROBABILITIES = {
    "retry_now": Decimal("0.45"), "retry_later": Decimal("0.42"),
    "payment_link": Decimal("0.35"), "alternate_payment_method": Decimal("0.40"),
    "customer_reminder": Decimal("0.25"), "human_escalation": Decimal("0.30"), "stop": Decimal("0"),
}
_FRICTION = {
    "retry_now": "low", "retry_later": "low", "payment_link": "moderate",
    "alternate_payment_method": "moderate", "customer_reminder": "high",
    "human_escalation": "high", "stop": "none",
}
_TIME = {
    "retry_now": "immediate", "retry_later": "6 hours", "payment_link": "24 hours",
    "alternate_payment_method": "24 hours", "customer_reminder": "12 hours",
    "human_escalation": "1 business day", "stop": "no further action",
}


class StrategySimulator:
    """Compute repeatable synthetic estimates; these are not real-world predictions."""

    def simulate(self, payment: Mapping[str, Any], diagnosis: DiagnosisResult, eligibility: Sequence[StrategyEligibility]) -> list[SimulatedStrategyOutcome]:
        """Simulate only currently eligible strategies, with a defensive method-capability check."""
        outcomes = []
        for item in eligibility:
            if not item.eligible or (item.strategy == "alternate_payment_method" and not self._has_alternative_method(payment)):
                continue
            outcomes.append(self._outcome(payment, diagnosis, item.strategy))
        return outcomes

    @staticmethod
    def _has_alternative_method(payment: Mapping[str, Any]) -> bool:
        return any(method != payment.get("payment_method") for method in payment.get("available_payment_methods", []))

    def _outcome(self, payment: Mapping[str, Any], diagnosis: DiagnosisResult, strategy: str) -> SimulatedStrategyOutcome:
        if strategy == "stop":
            return SimulatedStrategyOutcome(
                payment["payment_id"], "stop", True, Decimal("0"), Decimal("0"),
                _FRICTION["stop"], _TIME["stop"],
                "Stop is the zero-recovery safe fallback and does not attempt payment recovery.",
                ["Synthetic simulation estimate; not a real-world prediction.", "Stop performs no recovery action."],
            )
        probability = _BASE_PROBABILITIES[strategy]
        assumptions = ["Synthetic simulation estimate; not a real-world prediction.", f"Failure category: {diagnosis.failure_category.value}."]
        adjustment, category_rationale = self._category_adjustment(diagnosis.failure_category, strategy)
        probability += adjustment
        assumptions.append(category_rationale)

        history_adjustment = self._history_adjustment(payment)
        probability += history_adjustment
        if history_adjustment:
            assumptions.append(f"Customer payment-history adjustment: {history_adjustment:+.2f}.")

        retry_adjustment = Decimal("-0.08") * int(payment.get("retry_count", 0))
        probability += retry_adjustment
        if retry_adjustment:
            assumptions.append(f"Previous retry adjustment: {retry_adjustment:+.2f}.")
        if strategy == "retry_later":
            probability -= Decimal("0.08")
            assumptions.append("Timing/drop-off adjustment for the 6-hour delay: -0.08.")
        if strategy in {"payment_link", "customer_reminder"} and int(payment.get("customer_contact_count", 0)):
            probability -= Decimal("0.03") * int(payment["customer_contact_count"])
            assumptions.append("Prior customer-contact fatigue adjustment applied.")

        probability = max(Decimal("0"), min(Decimal("0.95"), probability)).quantize(Decimal("0.01"))
        amount = Decimal(str(payment["amount"]))
        expected = (amount * probability).quantize(Decimal("0.01"))
        rationale = f"{category_rationale} Base synthetic estimate for {strategy} is adjusted only by recorded history, retries, contacts, and timing."
        return SimulatedStrategyOutcome(payment["payment_id"], strategy, True, probability, expected, _FRICTION[strategy], _TIME[strategy], rationale, assumptions)

    @staticmethod
    def _history_adjustment(payment: Mapping[str, Any]) -> Decimal:
        successes = int(payment.get("successful_payment_count", 0))
        failures = int(payment.get("failed_payment_count", 0))
        if successes >= 5:
            return Decimal("0.05")
        if failures > successes:
            return Decimal("-0.05")
        return Decimal("0")

    @staticmethod
    def _category_adjustment(category: FailureCategory, strategy: str) -> tuple[Decimal, str]:
        adjustments = {
            FailureCategory.TEMPORARY_FAILURE: {"retry_now": Decimal("0.30"), "retry_later": Decimal("0.22"), "payment_link": Decimal("0.08"), "alternate_payment_method": Decimal("0.05"), "customer_reminder": Decimal("0.05")},
            FailureCategory.INSUFFICIENT_FUNDS: {"retry_now": Decimal("-0.15"), "retry_later": Decimal("0.15"), "payment_link": Decimal("0.12"), "alternate_payment_method": Decimal("0.12"), "customer_reminder": Decimal("0.10")},
            FailureCategory.EXPIRED_PAYMENT_METHOD: {"payment_link": Decimal("0.12"), "alternate_payment_method": Decimal("0.25"), "customer_reminder": Decimal("0.08"), "human_escalation": Decimal("0.05")},
            FailureCategory.PERMANENT_DECLINE: {"human_escalation": Decimal("-0.05")},
            FailureCategory.SUSPECTED_FRAUD: {"human_escalation": Decimal("-0.15")},
            FailureCategory.RECURRING_MANDATE_FAILURE: {"retry_now": Decimal("0.10"), "retry_later": Decimal("0.18"), "payment_link": Decimal("0.15"), "customer_reminder": Decimal("0.10"), "alternate_payment_method": Decimal("0.08")},
            FailureCategory.UNKNOWN: {},
        }
        adjustment = adjustments[category].get(strategy, Decimal("0"))
        return adjustment, f"Category-specific adjustment for {category.value}: {adjustment:+.2f}."
