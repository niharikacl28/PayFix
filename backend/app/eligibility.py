"""Deterministic strategy eligibility checks based only on stored payment facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from typing import Any, Mapping

from .models import Payment
from .recovery_config import DEFAULT_RECOVERY_CONFIG, RecoveryConfig
from .strategies import STRATEGY_LIBRARY, StrategyDefinition


PERMANENT_FAILURE_CATEGORIES = {"permanent_decline", "expired_card", "expired_payment_method", "suspected_fraud"}
RETRY_BLOCKED_CATEGORIES = {"permanent_decline", "expired_card", "expired_payment_method"}


@dataclass(frozen=True)
class StrategyEligibility:
    """A deterministic explanation of whether one controlled action is allowed."""

    strategy: str
    eligible: bool
    reason: str
    metadata: dict[str, Any]


def _payment_values(payment: Payment | Mapping[str, Any]) -> dict[str, Any]:
    """Accept either a model being created or a dictionary returned by the repository."""
    if is_dataclass(payment):
        return asdict(payment)
    return dict(payment)


def _is_high_risk(values: Mapping[str, Any]) -> bool:
    return values.get("risk_level") == "high" or values.get("failure_category") == "suspected_fraud"


def _is_permanent_failure(values: Mapping[str, Any]) -> bool:
    return values.get("failure_category") in PERMANENT_FAILURE_CATEGORIES


class EligibilityEngine:
    """Evaluate every allowed strategy without selecting or executing any of them."""

    def __init__(self, config: RecoveryConfig = DEFAULT_RECOVERY_CONFIG) -> None:
        self.config = config

    def evaluate(self, payment: Payment | Mapping[str, Any]) -> list[StrategyEligibility]:
        """Return an explanation for every strategy in the controlled library."""
        values = _payment_values(payment)
        return [self._evaluate_strategy(strategy, values) for strategy in STRATEGY_LIBRARY]

    def _evaluate_strategy(self, strategy: StrategyDefinition, values: Mapping[str, Any]) -> StrategyEligibility:
        handlers = {
            "retry_now": self._automatic_retry,
            "retry_later": self._automatic_retry,
            "payment_link": self._customer_communication,
            "alternate_payment_method": self._alternate_payment_method,
            "customer_reminder": self._customer_communication,
            "human_escalation": self._human_escalation,
            "stop": self._stop,
        }
        eligible, reason, metadata = handlers[strategy.name](values)
        return StrategyEligibility(strategy.name, eligible, reason, metadata)

    def _automatic_retry(self, values: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        metadata = self._limit_metadata(values)
        if values.get("payment_status") != "failed":
            return False, "Automatic retry is only applicable to failed payments.", metadata
        if _is_high_risk(values):
            return False, "Automatic retry is blocked for suspected fraud or high-risk payments.", metadata
        if _is_permanent_failure(values):
            return False, "Automatic retry is blocked because this is a permanent or hard failure.", metadata
        if not values.get("is_retryable", False):
            return False, "Automatic retry is blocked because the failure is marked non-retryable.", metadata
        if int(values.get("retry_count", 0)) >= self.config.max_automated_retries:
            return False, "Automatic retry limit has been reached for this payment.", metadata
        if metadata["automated_amount_exceeds_limit"]:
            return False, "Automatic recovery is blocked because the payment amount exceeds the configured limit.", metadata
        return True, "Failed, retryable payment is within configured automatic retry and amount limits.", metadata

    def _customer_communication(self, values: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        metadata = {
            "contact_count": int(values.get("customer_contact_count", 0)),
            "max_customer_contacts": self.config.max_customer_contacts,
            **self._limit_metadata(values),
        }
        if values.get("payment_status") != "failed":
            return False, "Customer communication is only applicable to failed payments.", metadata
        if _is_high_risk(values):
            return False, "Customer communication is not appropriate for a suspected fraud or high-risk case.", metadata
        if metadata["automated_amount_exceeds_limit"]:
            return False, "Automatic recovery is blocked because the payment amount exceeds the configured limit.", metadata
        if metadata["contact_count"] >= self.config.max_customer_contacts:
            return False, "Customer contact limit has been reached for this payment.", metadata
        return True, "A failed payment can receive customer-facing recovery communication within the contact limit.", metadata

    def _alternate_payment_method(self, values: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        available_methods = list(values.get("available_payment_methods") or [])
        alternatives = [method for method in available_methods if method != values.get("payment_method")]
        metadata = {
            "payment_method": values.get("payment_method"),
            "available_payment_methods": available_methods,
            "available_alternative_methods": alternatives,
            **self._limit_metadata(values),
        }
        if values.get("payment_status") != "failed":
            return False, "An alternate payment method is only applicable to failed payments.", metadata
        if _is_high_risk(values):
            return False, "Alternate payment method outreach is not appropriate for a suspected fraud or high-risk case.", metadata
        if metadata["automated_amount_exceeds_limit"]:
            return False, "Automatic recovery is blocked because the payment amount exceeds the configured limit.", metadata
        if not alternatives:
            return False, "No supported alternative payment method is recorded for this customer.", metadata
        return True, "Customer has recorded supported alternative payment method(s).", metadata

    def _human_escalation(self, values: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        metadata = self._limit_metadata(values)
        repeated_failures = int(values.get("failed_payment_count", 0)) > 1 or int(values.get("retry_count", 0)) >= self.config.max_automated_retries
        if _is_high_risk(values):
            return True, "Human escalation is appropriate for a suspected fraud or high-risk case.", metadata
        if metadata["automated_amount_exceeds_limit"]:
            return True, "Human escalation is appropriate because the amount exceeds the automated recovery limit.", metadata
        if _is_permanent_failure(values):
            return True, "Human escalation is appropriate for a permanent or hard failure.", metadata
        if repeated_failures:
            return True, "Human escalation is appropriate after repeated payment failures or exhausted retries.", metadata
        return False, "Human escalation is not currently required by the recorded risk, amount, or failure history.", metadata

    def _stop(self, values: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        return True, "Stop is always available as the safe fallback action.", {"safe_fallback": True}

    def _limit_metadata(self, values: Mapping[str, Any]) -> dict[str, Any]:
        amount = Decimal(str(values.get("amount", 0)))
        return {
            "retry_count": int(values.get("retry_count", 0)),
            "max_automated_retries": self.config.max_automated_retries,
            "amount": str(amount),
            "max_automated_recovery_amount": str(self.config.max_automated_recovery_amount),
            "automated_amount_exceeds_limit": amount > self.config.max_automated_recovery_amount,
        }


def evaluate_strategies(payment: Payment | Mapping[str, Any], config: RecoveryConfig = DEFAULT_RECOVERY_CONFIG) -> list[StrategyEligibility]:
    """Convenience function for callers that do not need to retain an engine instance."""
    return EligibilityEngine(config).evaluate(payment)
