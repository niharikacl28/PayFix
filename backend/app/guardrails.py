"""Deterministic backend guardrails for bounded simulated recovery actions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .eligibility import RETRY_BLOCKED_CATEGORIES, StrategyEligibility


AUTOMATED_STRATEGIES = {"retry_now", "retry_later", "payment_link", "alternate_payment_method", "customer_reminder"}
CONTACT_STRATEGIES = {"payment_link", "customer_reminder"}
RETRY_STRATEGIES = {"retry_now", "retry_later"}


@dataclass(frozen=True)
class GuardrailConfig:
    """Merchant-adjustable limits; defaults match the established eligibility policy."""

    max_automated_retries: int = 2
    max_automated_customer_contacts: int = 2
    max_automated_payment_amount: Decimal = Decimal("10000")
    block_high_risk_automation: bool = True


@dataclass(frozen=True)
class GuardrailResult:
    strategy: str
    allowed: bool
    reason: str
    checks: list[str]

    def to_dict(self) -> dict[str, object]:
        return {"strategy": self.strategy, "allowed": self.allowed, "reason": self.reason, "checks": self.checks}


class RecoveryGuardrails:
    """Authorize only eligible, policy-compliant simulated actions."""

    def __init__(self, config: GuardrailConfig = GuardrailConfig()) -> None:
        self.config = config

    def evaluate(self, payment: Mapping[str, Any], strategy: str, eligibility: Sequence[StrategyEligibility]) -> GuardrailResult:
        eligible = {item.strategy: item.eligible for item in eligibility}
        checks = ["Strategy eligibility was evaluated by the deterministic backend."]
        if strategy == "stop":
            return GuardrailResult(strategy, True, "Stop is always allowed as the safe fallback.", checks)
        if not eligible.get(strategy, False):
            return GuardrailResult(strategy, False, "Strategy is not eligible according to deterministic backend rules.", checks)
        if strategy == "human_escalation":
            return GuardrailResult(strategy, True, "Human escalation is permitted for simulated manual review.", checks)

        amount = Decimal(str(payment["amount"]))
        if self.config.block_high_risk_automation and (payment.get("risk_level") == "high" or payment.get("failure_category") == "suspected_fraud"):
            return GuardrailResult(strategy, False, "Automatic recovery is blocked for suspected fraud or high-risk payments.", checks)
        if strategy in RETRY_STRATEGIES and payment.get("failure_category") in RETRY_BLOCKED_CATEGORIES:
            return GuardrailResult(strategy, False, "Automatic retry is blocked for permanent payment failures.", checks)
        if amount > self.config.max_automated_payment_amount:
            return GuardrailResult(strategy, False, "Automatic recovery is blocked because the amount requires human escalation.", checks)
        if strategy in RETRY_STRATEGIES and int(payment.get("retry_count", 0)) >= self.config.max_automated_retries:
            return GuardrailResult(strategy, False, "Automatic retry limit has been reached.", checks)
        if strategy in CONTACT_STRATEGIES and int(payment.get("customer_contact_count", 0)) >= self.config.max_automated_customer_contacts:
            return GuardrailResult(strategy, False, "Automatic customer-contact limit has been reached.", checks)
        checks.append("Strategy is within automated amount, retry, contact, and risk limits.")
        return GuardrailResult(strategy, True, "Strategy is allowed by deterministic recovery guardrails.", checks)
