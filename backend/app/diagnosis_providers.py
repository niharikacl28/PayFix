"""Replaceable providers that explain failures but never select recovery actions."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Mapping

from .diagnosis_models import DiagnosisDraft, FailureCategory


class DiagnosisProvider(ABC):
    """Small contract implemented by deterministic and optional LLM providers."""

    @abstractmethod
    def diagnose(self, payment_context: Mapping[str, Any]) -> DiagnosisDraft:
        """Explain the supplied facts without changing them or suggesting actions."""


class DiagnosisProviderConfigurationError(RuntimeError):
    """Raised only when an optional external provider is explicitly selected incorrectly."""


_CATEGORY_MAPPING = {
    "temporary_network": FailureCategory.TEMPORARY_FAILURE,
    "temporary_failure": FailureCategory.TEMPORARY_FAILURE,
    "insufficient_funds": FailureCategory.INSUFFICIENT_FUNDS,
    "expired_card": FailureCategory.EXPIRED_PAYMENT_METHOD,
    "expired_payment_method": FailureCategory.EXPIRED_PAYMENT_METHOD,
    "permanent_decline": FailureCategory.PERMANENT_DECLINE,
    "suspected_fraud": FailureCategory.SUSPECTED_FRAUD,
    "recurring_mandate_failure": FailureCategory.RECURRING_MANDATE_FAILURE,
}


class DeterministicDiagnosisProvider(DiagnosisProvider):
    """Offline provider used by default and by tests; it only summarizes known facts."""

    def diagnose(self, payment_context: Mapping[str, Any]) -> DiagnosisDraft:
        category = _CATEGORY_MAPPING.get(str(payment_context.get("failure_category", "")), FailureCategory.UNKNOWN)
        reason = payment_context.get("failure_reason") or "No failure reason was recorded."
        retryable = bool(payment_context.get("is_retryable", False))
        cause, explanation = self._explanation(category, str(reason), retryable)
        risk = str(payment_context.get("risk_level") or "unknown")
        retries = int(payment_context.get("retry_count") or 0)
        contacts = int(payment_context.get("customer_contact_count") or 0)
        successes = int(payment_context.get("successful_payment_count") or 0)
        failures = int(payment_context.get("failed_payment_count") or 0)
        return DiagnosisDraft(
            failure_category=category,
            likely_cause=cause,
            confidence=0.9 if category is not FailureCategory.UNKNOWN else 0.25,
            explanation=explanation,
            retryability_assessment="The stored payment context marks this failure as retryable." if retryable else "The stored payment context does not mark this failure as retryable.",
            risk_observation=f"Recorded risk level is {risk}.",
            timing_observation=f"The payment has {retries} recorded retry attempt(s).",
            customer_context_observation=f"Recorded customer history: {successes} successful and {failures} failed payments; {contacts} recovery contact(s).",
        )

    @staticmethod
    def _explanation(category: FailureCategory, reason: str, retryable: bool) -> tuple[str, str]:
        messages = {
            FailureCategory.TEMPORARY_FAILURE: ("A temporary bank, issuer, or network interruption is likely.", f"The recorded failure reason is '{reason}', which appears temporary."),
            FailureCategory.INSUFFICIENT_FUNDS: ("The customer's available balance was likely insufficient.", f"The recorded failure reason is '{reason}'; repeated immediate retries may not help until funds change."),
            FailureCategory.EXPIRED_PAYMENT_METHOD: ("The payment method appears expired.", f"The recorded failure reason is '{reason}', indicating the payment method needs updating."),
            FailureCategory.PERMANENT_DECLINE: ("The issuer appears to have permanently declined the payment.", f"The recorded failure reason is '{reason}'; repeated retries may not be appropriate."),
            FailureCategory.SUSPECTED_FRAUD: ("The payment was likely blocked by fraud or risk controls.", f"The recorded failure reason is '{reason}'; automated recovery should be treated cautiously."),
            FailureCategory.RECURRING_MANDATE_FAILURE: ("The recurring payment mandate appears unavailable or invalid.", f"The recorded failure reason is '{reason}'; recurring mandate recovery differs from a one-time payment."),
            FailureCategory.UNKNOWN: ("The likely cause cannot be determined from the stored facts.", "No recognized failure category is recorded, so the cause cannot be determined and this is a conservative unknown diagnosis."),
        }
        cause, explanation = messages[category]
        if category is FailureCategory.TEMPORARY_FAILURE and not retryable:
            explanation += " The stored retryability flag is false, so eligibility remains authoritative."
        return cause, explanation


class OpenAIDiagnosisProvider(DiagnosisProvider):
    """Optional OpenAI-backed provider, constructed only when explicitly configured."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("PAYFIX_OPENAI_API_KEY")
        self.model = model or os.getenv("PAYFIX_OPENAI_MODEL", "gpt-4.1-mini")
        if not self.api_key:
            raise DiagnosisProviderConfigurationError("PAYFIX_OPENAI_API_KEY is required when PAYFIX_DIAGNOSIS_PROVIDER=openai.")
        try:
            from openai import OpenAI
        except ImportError as error:
            raise DiagnosisProviderConfigurationError("Install the optional 'openai' package to use the OpenAI diagnosis provider.") from error
        self.client = OpenAI(api_key=self.api_key)

    def diagnose(self, payment_context: Mapping[str, Any]) -> DiagnosisDraft:
        prompt = (
            "Diagnose only the supplied failed-payment facts. Do not invent facts, payment methods, "
            "strategies, actions, or eligibility. Return JSON with: failure_category, likely_cause, confidence, "
            "explanation, retryability_assessment, risk_observation, timing_observation, customer_context_observation. "
            f"failure_category must be one of: {[item.value for item in FailureCategory]}.\n"
            f"Payment context: {json.dumps(dict(payment_context), default=str)}"
        )
        response = self.client.responses.create(model=self.model, input=prompt)
        try:
            data = json.loads(response.output_text)
            category_value = data.get("failure_category", "unknown")
            try:
                category = FailureCategory(category_value)
            except ValueError:
                category = FailureCategory.UNKNOWN
            return DiagnosisDraft(
                failure_category=category,
                likely_cause=str(data["likely_cause"]), confidence=float(data["confidence"]),
                explanation=str(data["explanation"]), retryability_assessment=str(data["retryability_assessment"]),
                risk_observation=str(data["risk_observation"]), timing_observation=str(data["timing_observation"]),
                customer_context_observation=str(data["customer_context_observation"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DiagnosisProviderConfigurationError("OpenAI diagnosis provider returned an invalid structured diagnosis.") from error


def get_diagnosis_provider() -> DiagnosisProvider:
    """Select the configured provider; mock is intentionally the safe default."""
    provider_name = os.getenv("PAYFIX_DIAGNOSIS_PROVIDER", "mock").lower()
    if provider_name == "mock":
        return DeterministicDiagnosisProvider()
    if provider_name == "openai":
        return OpenAIDiagnosisProvider()
    raise DiagnosisProviderConfigurationError("PAYFIX_DIAGNOSIS_PROVIDER must be 'mock' or 'openai'.")
