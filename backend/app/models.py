"""Typed data records for the PayFix persistence layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4


def utc_now() -> str:
    """Return an unambiguous UTC timestamp suitable for SQLite text storage."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Payment:
    customer_id: str
    merchant_id: str
    amount: Decimal
    payment_status: str
    payment_method: str
    available_payment_methods: list[str]
    payment_id: str = field(default_factory=lambda: f"pay_{uuid4().hex}")
    currency: str = "INR"
    failure_reason: Optional[str] = None
    failure_category: Optional[str] = None
    is_retryable: bool = False
    risk_level: str = "low"
    successful_payment_count: int = 0
    failed_payment_count: int = 0
    customer_lifetime_value: Decimal = Decimal("0")
    last_successful_payment_at: Optional[str] = None
    retry_count: int = 0
    customer_contact_count: int = 0
    recovery_status: str = "not_started"
    recovered_amount: Decimal = Decimal("0")
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class RecoveryAttempt:
    payment_id: str
    strategy: str
    attempt_id: str = field(default_factory=lambda: f"attempt_{uuid4().hex}")
    scheduled_at: Optional[str] = None
    status: str = "pending"
    result: Optional[str] = None
    recovered_amount: Decimal = Decimal("0")
    reason: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    completed_at: Optional[str] = None


@dataclass
class AuditLog:
    payment_id: str
    event_type: str
    event_details: str
    audit_id: str = field(default_factory=lambda: f"audit_{uuid4().hex}")
    diagnosis: Optional[str] = None
    strategies_considered: list[str] = field(default_factory=list)
    selected_action: Optional[str] = None
    action_rationale: Optional[str] = None
    guardrails_passed: Optional[bool] = None
    execution_result: Optional[str] = None
    recovered_amount: Decimal = Decimal("0")
    created_at: str = field(default_factory=utc_now)
