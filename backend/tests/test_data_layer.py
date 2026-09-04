"""Tests for the local PayFix SQLite persistence layer."""

import sqlite3
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.database import initialize_database
from app.demo_data import load_demo_data
from app.models import AuditLog, Payment, RecoveryAttempt
from app.repositories import PayFixRepository


class DataLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-payfix-{uuid4().hex}.db"
        self.repository = PayFixRepository(self.database_path)
        self.payment = Payment("customer_test", "merchant_test", Decimal("1250.50"), "failed", "upi", ["upi"], "pay_test_001", failure_reason="Temporary network error", failure_category="temporary_network", is_retryable=True)

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    def test_initialization_creates_database_and_tables(self) -> None:
        self.assertEqual(initialize_database(self.database_path), self.database_path)
        self.assertTrue(self.database_path.exists())
        connection = sqlite3.connect(self.database_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            connection.close()
        self.assertTrue({"payments", "recovery_attempts", "audit_logs"}.issubset(tables))

    def test_insert_and_retrieve_payment(self) -> None:
        self.repository.create_payment(self.payment)
        saved = self.repository.get_payment(self.payment.payment_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["customer_id"], "customer_test")
        self.assertEqual(saved["available_payment_methods"], ["upi"])
        self.assertTrue(saved["is_retryable"])

    def test_create_recovery_attempt_and_audit_log(self) -> None:
        self.repository.create_payment(self.payment)
        self.repository.create_recovery_attempt(RecoveryAttempt(self.payment.payment_id, "customer_reminder", reason="Synthetic test"))
        self.repository.create_audit_log(AuditLog(self.payment.payment_id, "payment_diagnosed", "Recorded source failure context", diagnosis="Temporary network error", strategies_considered=["retry_later", "customer_reminder"]))
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM recovery_attempts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0], 1)
        finally:
            connection.close()

    def test_update_payment_persists_recovery_fields(self) -> None:
        self.repository.create_payment(self.payment)
        saved = self.repository.update_payment(self.payment.payment_id, recovery_status="simulated_recovered", recovered_amount=Decimal("1250.50"), retry_count=1)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["recovery_status"], "simulated_recovered")
        self.assertEqual(saved["retry_count"], 1)
        self.assertEqual(self.repository.list_recovery_attempts(self.payment.payment_id), [])

    def test_load_demo_data_is_varied_and_idempotent(self) -> None:
        self.assertEqual(load_demo_data(self.database_path), 6)
        self.assertEqual(load_demo_data(self.database_path), 0)
        payments = self.repository.list_payments()
        self.assertEqual(len(payments), 6)
        self.assertIn("suspected_fraud", {payment["failure_category"] for payment in payments})
        expired_card = next(payment for payment in payments if payment["payment_id"] == "pay_demo_expired")
        self.assertEqual(expired_card["available_payment_methods"], ["upi"])


if __name__ == "__main__":
    unittest.main()
