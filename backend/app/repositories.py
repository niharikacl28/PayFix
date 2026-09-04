"""Small repositories that persist PayFix facts without making recovery decisions."""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .database import get_connection, initialize_database
from .models import AuditLog, Payment, RecoveryAttempt, utc_now

_PAYMENT_UPDATABLE = {
    "customer_id",
    "merchant_id",
    "amount",
    "currency",
    "payment_status",
    "payment_method",
    "available_payment_methods",
    "failure_reason",
    "failure_category",
    "is_retryable",
    "risk_level",
    "successful_payment_count",
    "failed_payment_count",
    "customer_lifetime_value",
    "last_successful_payment_at",
    "retry_count",
    "customer_contact_count",
    "recovery_status",
    "recovered_amount",
    "updated_at",
}
_ATTEMPT_UPDATABLE = {"strategy", "scheduled_at", "status", "result", "recovered_amount", "reason", "completed_at"}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, bool):
        return int(value)
    return value


def _as_database_values(record: object) -> dict[str, Any]:
    return {key: _serialize_value(value) for key, value in asdict(record).items()}


def _row_to_dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("available_payment_methods", "strategies_considered"):
        if key in result:
            result[key] = json.loads(result[key])
    for key in ("is_retryable", "guardrails_passed"):
        if key in result and result[key] is not None:
            result[key] = bool(result[key])
    return result


class PayFixRepository:
    """Persistence operations for payments, attempts, and their audit trail."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = initialize_database(database_path)

    def create_payment(self, payment: Payment) -> Payment:
        values = _as_database_values(payment)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{column}" for column in values)
        with get_connection(self.database_path) as connection:
            connection.execute(f"INSERT INTO payments ({columns}) VALUES ({placeholders})", values)
        return payment

    def get_payment(self, payment_id: str) -> dict[str, Any] | None:
        with get_connection(self.database_path) as connection:
            row = connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def list_payments(self) -> list[dict[str, Any]]:
        with get_connection(self.database_path) as connection:
            rows = connection.execute("SELECT * FROM payments ORDER BY created_at, payment_id").fetchall()
        return [_row_to_dict(row) for row in rows]

    def update_payment(self, payment_id: str, **fields: Any) -> dict[str, Any] | None:
        if self.get_payment(payment_id) is None:
            return None
        values = {key: _serialize_value(value) for key, value in fields.items() if key in _PAYMENT_UPDATABLE}
        values.setdefault("updated_at", utc_now())
        assignments = ", ".join(f"{column} = :{column}" for column in values)
        values["payment_id"] = payment_id
        with get_connection(self.database_path) as connection:
            connection.execute(f"UPDATE payments SET {assignments} WHERE payment_id = :payment_id", values)
        return self.get_payment(payment_id)

    def create_recovery_attempt(self, attempt: RecoveryAttempt) -> RecoveryAttempt:
        values = _as_database_values(attempt)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{column}" for column in values)
        with get_connection(self.database_path) as connection:
            connection.execute(f"INSERT INTO recovery_attempts ({columns}) VALUES ({placeholders})", values)
        return attempt

    def update_recovery_attempt(self, attempt_id: str, **fields: Any) -> dict[str, Any] | None:
        values = {key: _serialize_value(value) for key, value in fields.items() if key in _ATTEMPT_UPDATABLE}
        if not values:
            return self.get_recovery_attempt(attempt_id)
        assignments = ", ".join(f"{column} = :{column}" for column in values)
        values["attempt_id"] = attempt_id
        with get_connection(self.database_path) as connection:
            connection.execute(f"UPDATE recovery_attempts SET {assignments} WHERE attempt_id = :attempt_id", values)
        return self.get_recovery_attempt(attempt_id)

    def get_recovery_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with get_connection(self.database_path) as connection:
            row = connection.execute("SELECT * FROM recovery_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def list_recovery_attempts(self, payment_id: str) -> list[dict[str, Any]]:
        with get_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM recovery_attempts WHERE payment_id = ? ORDER BY created_at, attempt_id",
                (payment_id,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def create_audit_log(self, audit_log: AuditLog) -> AuditLog:
        values = _as_database_values(audit_log)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{column}" for column in values)
        with get_connection(self.database_path) as connection:
            connection.execute(f"INSERT INTO audit_logs ({columns}) VALUES ({placeholders})", values)
        return audit_log

    def list_audit_logs(self, payment_id: str) -> list[dict[str, Any]]:
        with get_connection(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM audit_logs WHERE payment_id = ? ORDER BY created_at, audit_id",
                (payment_id,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]
