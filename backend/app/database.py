"""SQLite connection and schema management for PayFix.

This module deliberately contains persistence concerns only.  Recovery decisions,
eligibility checks, and payment execution will be added in later milestones.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "payfix.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    amount NUMERIC NOT NULL CHECK(amount >= 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    payment_status TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    available_payment_methods TEXT NOT NULL DEFAULT '[]',
    failure_reason TEXT,
    failure_category TEXT,
    is_retryable INTEGER NOT NULL DEFAULT 0 CHECK(is_retryable IN (0, 1)),
    risk_level TEXT NOT NULL DEFAULT 'low',
    successful_payment_count INTEGER NOT NULL DEFAULT 0 CHECK(successful_payment_count >= 0),
    failed_payment_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_payment_count >= 0),
    customer_lifetime_value NUMERIC NOT NULL DEFAULT 0 CHECK(customer_lifetime_value >= 0),
    last_successful_payment_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    customer_contact_count INTEGER NOT NULL DEFAULT 0 CHECK(customer_contact_count >= 0),
    recovery_status TEXT NOT NULL DEFAULT 'not_started',
    recovered_amount NUMERIC NOT NULL DEFAULT 0 CHECK(recovered_amount >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_merchant_status
    ON payments (merchant_id, payment_status);
CREATE INDEX IF NOT EXISTS idx_payments_recovery_status
    ON payments (recovery_status);

CREATE TABLE IF NOT EXISTS recovery_attempts (
    attempt_id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    scheduled_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    recovered_amount NUMERIC NOT NULL DEFAULT 0 CHECK(recovered_amount >= 0),
    reason TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recovery_attempts_payment
    ON recovery_attempts (payment_id, created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_details TEXT NOT NULL,
    diagnosis TEXT,
    strategies_considered TEXT NOT NULL DEFAULT '[]',
    selected_action TEXT,
    action_rationale TEXT,
    guardrails_passed INTEGER CHECK(guardrails_passed IN (0, 1)),
    execution_result TEXT,
    recovered_amount NUMERIC NOT NULL DEFAULT 0 CHECK(recovered_amount >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_payment
    ON audit_logs (payment_id, created_at);

-- Immutable snapshot of the original decision recorded when a POST /recover
-- executes. Used by the read-only GET /decision endpoint so re-opening a
-- recovered payment does not recompute diagnosis/optimization against the
-- mutated row. One row per payment; written only by RecoveryService.
CREATE TABLE IF NOT EXISTS decision_snapshots (
    payment_id TEXT PRIMARY KEY,
    diagnosis_json TEXT NOT NULL,
    optimization_json TEXT NOT NULL,
    selected_strategy TEXT NOT NULL,
    expected_recovered_amount NUMERIC NOT NULL CHECK(expected_recovered_amount >= 0),
    selection_reason TEXT NOT NULL,
    guardrail_json TEXT NOT NULL,
    execution_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
);
"""


def get_database_path(database_path: str | Path | None = None) -> Path:
    """Resolve an explicit path or the configurable local database location."""
    if database_path is not None:
        return Path(database_path)
    return Path(os.getenv("PAYFIX_DATABASE_PATH", DEFAULT_DATABASE_PATH))


@contextmanager
def get_connection(database_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a configured SQLite connection and commit successful changes."""
    path = get_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(database_path: str | Path | None = None) -> Path:
    """Create all PayFix tables if they do not already exist and return the path."""
    path = get_database_path(database_path)
    with get_connection(path) as connection:
        connection.executescript(SCHEMA)
    return path
