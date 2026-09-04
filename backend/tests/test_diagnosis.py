"""Tests for offline, structured payment-failure diagnosis."""

import os
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.diagnosis_models import DiagnosisDraft, FailureCategory
from app.diagnosis_providers import DeterministicDiagnosisProvider, DiagnosisProvider, get_diagnosis_provider
from app.diagnosis_service import DiagnosisService
from app.models import Payment
from app.repositories import PayFixRepository


class MisleadingProvider(DiagnosisProvider):
    """Represents a provider that discusses a method it cannot make eligible."""

    def diagnose(self, payment_context: dict[str, object]) -> DiagnosisDraft:
        return DiagnosisDraft(FailureCategory.TEMPORARY_FAILURE, "Temporary issue", 0.5, "An alternate card might help.", "Retryability is unknown.", "Risk is low.", "No timing inference.", "No additional context.")


class DiagnosisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-diagnosis-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        self.service = DiagnosisService(self.repository, DeterministicDiagnosisProvider())

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def diagnose(self, **overrides: object):
        values: dict[str, object] = {
            "payment_id": f"pay_{uuid4().hex}", "customer_id": "customer_1", "merchant_id": "merchant_1",
            "amount": Decimal("5000"), "payment_status": "failed", "payment_method": "upi",
            "available_payment_methods": ["upi"], "risk_level": "low", "retry_count": 0,
            "customer_contact_count": 0, "successful_payment_count": 2, "failed_payment_count": 0,
        }
        values.update(overrides)
        payment = Payment(**values)  # type: ignore[arg-type]
        self.repository.create_payment(payment)
        return self.service.diagnose_payment(payment.payment_id)

    def test_temporary_network_failure(self) -> None:
        result = self.diagnose(failure_category="temporary_network", failure_reason="Issuer timeout", is_retryable=True)
        self.assertEqual(result.failure_category, FailureCategory.TEMPORARY_FAILURE)
        self.assertIn("temporary", result.explanation.lower())
        self.assertIn("retry_now", result.eligible_strategy_names)

    def test_insufficient_funds_diagnosis(self) -> None:
        result = self.diagnose(failure_category="insufficient_funds", failure_reason="Insufficient funds", is_retryable=True)
        self.assertEqual(result.failure_category, FailureCategory.INSUFFICIENT_FUNDS)
        self.assertIn("balance", result.likely_cause.lower())
        self.assertIn("immediate", result.explanation.lower())

    def test_permanent_decline_diagnosis(self) -> None:
        result = self.diagnose(failure_category="permanent_decline", failure_reason="Permanent issuer decline", is_retryable=False)
        self.assertEqual(result.failure_category, FailureCategory.PERMANENT_DECLINE)
        self.assertNotIn("retry_now", result.eligible_strategy_names)

    def test_suspected_fraud_diagnosis(self) -> None:
        result = self.diagnose(failure_category="suspected_fraud", failure_reason="Fraud check", risk_level="high", is_retryable=False)
        self.assertEqual(result.failure_category, FailureCategory.SUSPECTED_FRAUD)
        self.assertIn("cautiously", result.explanation.lower())
        self.assertIn("human_escalation", result.eligible_strategy_names)

    def test_recurring_mandate_failure_diagnosis(self) -> None:
        result = self.diagnose(failure_category="recurring_mandate_failure", failure_reason="Mandate unavailable", is_retryable=True)
        self.assertEqual(result.failure_category, FailureCategory.RECURRING_MANDATE_FAILURE)
        self.assertIn("recurring", result.explanation.lower())

    def test_upi_only_does_not_gain_an_invented_card_strategy(self) -> None:
        result = self.diagnose(failure_category="temporary_network", is_retryable=True)
        self.assertNotIn("alternate_payment_method", result.eligible_strategy_names)

    def test_upi_plus_debit_card_allows_recorded_alternative(self) -> None:
        result = self.diagnose(failure_category="temporary_network", is_retryable=True, available_payment_methods=["upi", "debit_card"])
        self.assertIn("alternate_payment_method", result.eligible_strategy_names)

    def test_mock_provider_needs_no_api_key(self) -> None:
        with patch.dict(os.environ, {"PAYFIX_DIAGNOSIS_PROVIDER": "mock"}, clear=True):
            self.assertIsInstance(get_diagnosis_provider(), DeterministicDiagnosisProvider)

    def test_provider_cannot_override_deterministic_eligibility(self) -> None:
        service = DiagnosisService(self.repository, MisleadingProvider())
        payment = Payment("customer_2", "merchant_1", Decimal("5000"), "failed", "upi", ["upi"], failure_category="temporary_network", is_retryable=True)
        self.repository.create_payment(payment)
        result = service.diagnose_payment(payment.payment_id)
        self.assertNotIn("alternate_payment_method", result.eligible_strategy_names)

    def test_unknown_failure_is_safe(self) -> None:
        result = self.diagnose(failure_category=None, failure_reason=None, is_retryable=False)
        self.assertEqual(result.failure_category, FailureCategory.UNKNOWN)
        self.assertIn("cannot be determined", result.explanation.lower())

    def test_structured_result_is_json_ready(self) -> None:
        result = self.diagnose(failure_category="temporary_network", is_retryable=True)
        payload = result.to_dict()
        self.assertEqual(payload["payment_id"], result.payment_id)
        self.assertEqual(payload["failure_category"], "temporary_failure")


if __name__ == "__main__":
    unittest.main()
