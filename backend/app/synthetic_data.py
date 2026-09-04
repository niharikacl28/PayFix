"""Deterministic synthetic payment generator for batch evaluation.

Generates realistic *failed* payment records (no real customer data) that exercise
every supported failure category, retry history, contact history, and risk band.
Outputs go through the existing ``PayFixRepository.create_payment`` path so the
schema, eligibility rules, and downstream optimizer/simulator/executor all see
the same fields they see for real payments.

The generator is fully reproducible: given the same ``seed`` and ``count`` it
produces identical ``Payment`` instances in identical order.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Sequence

from .models import Payment
from .repositories import PayFixRepository


# ---------------------------------------------------------------------------
# Distribution tables. The percentages are guidelines, not hard contracts;
# the generator honors them within rounding tolerance and is reproducible.
# ---------------------------------------------------------------------------

# Map the user-facing category names in the task spec to the enum values that
# DiagnosisProvider / EligibilityEngine already understand.
FAILURE_CATEGORY_DISTRIBUTION: tuple[tuple[str, float], ...] = (
    ("temporary_network", 0.25),
    ("insufficient_funds", 0.20),
    ("expired_card", 0.15),
    ("recurring_mandate_failure", 0.15),
    ("permanent_decline", 0.10),
    ("suspected_fraud", 0.05),
    ("unknown", 0.10),
)

VALID_FAILURE_CATEGORIES: frozenset[str] = frozenset(
    name for name, _ in FAILURE_CATEGORY_DISTRIBUTION
)

# Methods actually used in the Indian payments context that the existing
# eligibility + simulator code already accept.
VALID_PAYMENT_METHODS: tuple[str, ...] = ("upi", "card", "netbanking", "wallet")

# Reason strings vary per category so generated data looks realistic to anyone
# inspecting it (and so diagnosis provider explanations have plausible inputs).
_FAILURE_REASONS: dict[str, tuple[str, ...]] = {
    "temporary_network": (
        "Issuer network timeout",
        "Bank gateway unavailable",
        "UPI collect request expired",
        "Temporary processor error",
        "Connectivity issue at issuer",
    ),
    "insufficient_funds": (
        "Insufficient funds in account",
        "Available balance below payment amount",
        "Card limit reached for the day",
    ),
    "expired_card": (
        "Card expired",
        "Card validity period ended",
        "Saved payment method no longer valid",
    ),
    "recurring_mandate_failure": (
        "Recurring UPI mandate unavailable",
        "Auto-debit mandate paused by customer",
        "Standing instruction rejected by issuer",
        "Recurring card mandate expired",
    ),
    "permanent_decline": (
        "Issuer permanently declined payment",
        "Card closed by issuer",
        "Do-not-honor status from issuer",
    ),
    "suspected_fraud": (
        "Transaction flagged for suspected fraud",
        "Risk engine blocked the payment",
        "Velocity limits exceeded at issuer",
    ),
    "unknown": (
        "Unknown processor failure",
        "Unclassified payment failure",
    ),
}

# Risk levels vary with the category. Suspected fraud is always high; permanent
# declines lean medium; recurring mandate failures are usually low.
_RISK_BY_CATEGORY: dict[str, str] = {
    "temporary_network": "low",
    "insufficient_funds": "low",
    "expired_card": "medium",
    "recurring_mandate_failure": "low",
    "permanent_decline": "medium",
    "suspected_fraud": "high",
    "unknown": "low",
}

# Whether the failure is marked retryable in stored facts. The DiagnosisProvider
# + EligibilityEngine consume this flag, so we want it to be plausible.
_IS_RETRYABLE_BY_CATEGORY: dict[str, bool] = {
    "temporary_network": True,
    "insufficient_funds": True,
    "expired_card": False,
    "recurring_mandate_failure": True,
    "permanent_decline": False,
    "suspected_fraud": False,
    "unknown": False,
}

# Probability that the customer has a recorded alternative payment method.
# Without alternatives, the simulator / eligibility will block the
# alternate_payment_method strategy, which is realistic for UPI-only users.
_ALT_METHOD_PROBABILITY: dict[str, float] = {
    "upi": 0.15,
    "card": 0.65,
    "netbanking": 0.30,
    "wallet": 0.40,
}

_DEFAULT_ALTERNATES_BY_METHOD: dict[str, tuple[str, ...]] = {
    "upi": ("upi", "card"),
    "card": ("card", "upi", "netbanking"),
    "netbanking": ("netbanking", "card"),
    "wallet": ("wallet", "upi"),
}

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyntheticDataConfig:
    """Configurable knobs for the synthetic generator.

    Defaults are calibrated to exercise the EligibilityEngine / StrategySimulator
    the way real-world traffic does.
    """

    seed: int = 20240904
    merchant_id: str = "merchant_synth"
    count: int = 500
    # A spread of 14 days back from "now" so created_at timestamps look realistic
    # without leaking current real-world dates into the generated dataset.
    timespan_days: int = 14
    # The lowest amount a generated payment can carry (inclusive).
    min_amount: Decimal = Decimal("99")
    # The highest amount a generated payment can carry (inclusive). The
    # EligibilityEngine blocks automated retry above 10000; we keep most
    # payments under that ceiling and sprinkle a few larger ones above it.
    max_amount: Decimal = Decimal("34999")
    # Probability that any given payment exceeds the 10000 automated-recovery
    # ceiling (exercises the "human escalation" path).
    high_value_probability: float = 0.10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weighted_choice(rng: random.Random, options: Sequence[tuple[str, float]]) -> str:
    """Pick one option from a (value, weight) sequence using the supplied RNG."""
    total = sum(weight for _, weight in options)
    pick = rng.uniform(0.0, total)
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if pick <= cumulative:
            return value
    return options[-1][0]


def _bounded_int(rng: random.Random, low: int, high: int) -> int:
    """Inclusive integer in [low, high]."""
    return rng.randint(low, high)


def _choose_payment_method(rng: random.Random, failure_category: str) -> str:
    """Pick a primary payment method weighted by failure-category affinity.

    Mirrors real Indian merchant traffic: UPI dominates most categories except
    recurring mandates (which lean card / netbanking) and permanent declines
    (often card-on-file issues).
    """
    weights: tuple[tuple[str, float], ...]
    if failure_category == "recurring_mandate_failure":
        weights = (("card", 0.45), ("upi", 0.25), ("netbanking", 0.25), ("wallet", 0.05))
    elif failure_category == "permanent_decline":
        weights = (("card", 0.65), ("upi", 0.15), ("netbanking", 0.15), ("wallet", 0.05))
    elif failure_category == "expired_card":
        weights = (("card", 0.85), ("upi", 0.10), ("netbanking", 0.05), ("wallet", 0.0))
    else:
        weights = (("upi", 0.55), ("card", 0.25), ("netbanking", 0.10), ("wallet", 0.10))
    return _weighted_choice(rng, weights)


def _choose_available_methods(
    rng: random.Random,
    primary: str,
    failure_category: str,
) -> list[str]:
    """Build the recorded ``available_payment_methods`` list for a customer."""
    base = list(_DEFAULT_ALTERNATES_BY_METHOD[primary])
    if rng.random() < _ALT_METHOD_PROBABILITY[primary]:
        # Add a second alternative for the more "method-rich" customers.
        if failure_category == "expired_card":
            base.append("netbanking")
        elif "wallet" not in base:
            base.append("wallet")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for method in base:
        if method not in seen:
            seen.add(method)
            ordered.append(method)
    return ordered


def _amount_for(
    rng: random.Random,
    failure_category: str,
    config: SyntheticDataConfig,
) -> Decimal:
    """Sample a realistic failed-payment amount."""
    # Different failure categories have characteristic amount distributions.
    if failure_category == "recurring_mandate_failure":
        # Subscriptions: low and tightly clustered.
        low = max(config.min_amount, Decimal("149"))
        high = min(config.max_amount, Decimal("1499"))
    elif failure_category == "insufficient_funds":
        # Tends to be mid-tier cart sizes.
        low = max(config.min_amount, Decimal("499"))
        high = min(config.max_amount, Decimal("7999"))
    elif failure_category == "permanent_decline":
        # High-value, often large orders that get declined.
        low = max(config.min_amount, Decimal("2999"))
        high = config.max_amount
    elif failure_category == "suspected_fraud":
        # Often unusually large amounts that triggered risk controls.
        low = max(config.min_amount, Decimal("1999"))
        high = config.max_amount
    elif failure_category == "expired_card":
        low = max(config.min_amount, Decimal("299"))
        high = min(config.max_amount, Decimal("5999"))
    else:  # temporary_network, unknown
        low = config.min_amount
        high = min(config.max_amount, Decimal("9999"))

    # Force a fraction of payments above the automated-recovery ceiling so we
    # exercise the human_escalation path; otherwise stay within it.
    if rng.random() < config.high_value_probability:
        low = max(low, Decimal("10001"))
        high = config.max_amount

    amount = Decimal(str(rng.randint(int(low), int(high))))
    # Add cents so the amount column has realistic precision.
    cents = Decimal(str(_bounded_int(rng, 0, 99))).quantize(Decimal("0.01"))
    return (amount + cents).quantize(Decimal("0.01"))


def _retries_for(rng: random.Random, failure_category: str, is_retryable: bool) -> int:
    """Pick a recorded retry count."""
    if not is_retryable:
        return 0
    if failure_category in {"temporary_network", "recurring_mandate_failure"}:
        return _bounded_int(rng, 0, 2)
    if failure_category == "insufficient_funds":
        return _bounded_int(rng, 0, 2)
    return _bounded_int(rng, 0, 1)


def _contact_count_for(rng: random.Random, is_retryable: bool) -> int:
    if not is_retryable:
        return _bounded_int(rng, 0, 2)
    return _bounded_int(rng, 0, 2)


def _success_history_for(rng: random.Random, failure_category: str) -> int:
    """How many successful payments this customer has on file."""
    if failure_category == "recurring_mandate_failure":
        # Recurring payers have a longer success trail.
        return _bounded_int(rng, 4, 30)
    if failure_category == "suspected_fraud":
        return _bounded_int(rng, 0, 25)
    return _bounded_int(rng, 0, 15)


def _failure_history_for(rng: random.Random, failure_category: str) -> int:
    if failure_category == "permanent_decline":
        return _bounded_int(rng, 1, 5)
    if failure_category == "recurring_mandate_failure":
        return _bounded_int(rng, 0, 4)
    return _bounded_int(rng, 0, 2)


def _lifetime_value_for(rng: random.Random, successful_payments: int, amount: Decimal) -> Decimal:
    """Rough customer lifetime value: a multiple of historical successful payments."""
    if successful_payments == 0:
        return Decimal("0")
    multiplier_low, multiplier_high = 0.5, 3.5
    multiplier = rng.uniform(multiplier_low, multiplier_high)
    return (amount * Decimal(str(multiplier)) * Decimal(str(successful_payments))).quantize(Decimal("0.01"))


def _created_at_for(
    rng: random.Random,
    config: SyntheticDataConfig,
    base_now: datetime,
) -> str:
    """Return an ISO-8601 UTC timestamp within the configured timespan."""
    span_seconds = int(config.timespan_days * 24 * 60 * 60)
    delta = rng.randint(0, span_seconds)
    timestamp = base_now - timedelta(seconds=delta)
    return timestamp.isoformat()


def _customer_id_for(rng: random.Random, used_ids: set[str]) -> str:
    """Generate a synthetic, unique-looking customer ID."""
    while True:
        candidate = f"cust_syn_{rng.randint(10_000, 99_999_999):08d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate


def _payment_id_for(rng: random.Random, index: int, used_ids: set[str]) -> str:
    """Generate a clearly-synthetic, unique payment ID.

    Uses ``pay_syn_<index>_<rand>`` so the dataset is obviously synthetic while
    staying unique under the index + random suffix combination.
    """
    while True:
        candidate = f"pay_syn_{index:05d}_{rng.randint(0x1000, 0xFFFF):04x}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate


def _last_successful_payment_at(
    rng: random.Random,
    successful_payments: int,
    config: SyntheticDataConfig,
    created_at: str,
    base_now: datetime,
) -> str | None:
    if successful_payments == 0:
        return None
    span_seconds = int(config.timespan_days * 24 * 60 * 60)
    delta = rng.randint(0, span_seconds)
    candidate = base_now - timedelta(seconds=delta)
    created_dt = datetime.fromisoformat(created_at)
    if candidate > created_dt:
        candidate = created_dt - timedelta(seconds=_bounded_int(rng, 1, span_seconds))
    return candidate.isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_payments(
    count: int = 500,
    seed: int = 20240904,
    config: SyntheticDataConfig | None = None,
) -> List[Payment]:
    """Build ``count`` synthetic *failed* ``Payment`` instances.

    Args:
        count: How many payments to produce. Must be non-negative.
        seed: Random seed for reproducibility.
        config: Optional overrides for merchant, amount range, timespan, etc.

    Returns:
        A list of ``Payment`` objects in generated order. None are persisted;
        call ``seed_synthetic_data`` to write them to a repository.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    cfg = config or SyntheticDataConfig(seed=seed, count=count)
    rng = random.Random(cfg.seed)

    # Use the seed itself to derive a deterministic reference timestamp so two
    # runs with the same seed produce byte-identical created_at /
    # last_successful_payment_at strings, regardless of when they execute.
    reference_epoch_seconds = 1_700_000_000 + (cfg.seed % (365 * 24 * 60 * 60))
    base_now = datetime.fromtimestamp(reference_epoch_seconds, tz=timezone.utc)

    used_customer_ids: set[str] = set()
    used_payment_ids: set[str] = set()
    payments: List[Payment] = []

    for index in range(count):
        failure_category = _weighted_choice(rng, FAILURE_CATEGORY_DISTRIBUTION)
        risk_level = _RISK_BY_CATEGORY[failure_category]
        is_retryable = _IS_RETRYABLE_BY_CATEGORY[failure_category]

        payment_method = _choose_payment_method(rng, failure_category)
        available_methods = _choose_available_methods(rng, payment_method, failure_category)

        amount = _amount_for(rng, failure_category, cfg)
        successful_payments = _success_history_for(rng, failure_category)
        failed_payments = _failure_history_for(rng, failure_category)
        retry_count = _retries_for(rng, failure_category, is_retryable)
        contact_count = _contact_count_for(rng, is_retryable)

        created_at = _created_at_for(rng, cfg, base_now)
        last_success_at = _last_successful_payment_at(
            rng, successful_payments, cfg, created_at, base_now
        )
        customer_lifetime_value = _lifetime_value_for(rng, successful_payments, amount)
        failure_reason = rng.choice(_FAILURE_REASONS[failure_category])
        customer_id = _customer_id_for(rng, used_customer_ids)
        payment_id = _payment_id_for(rng, index, used_payment_ids)

        payments.append(
            Payment(
                customer_id=customer_id,
                merchant_id=cfg.merchant_id,
                amount=amount,
                payment_status="failed",
                payment_method=payment_method,
                available_payment_methods=available_methods,
                payment_id=payment_id,
                currency="INR",
                failure_reason=failure_reason,
                failure_category=failure_category,
                is_retryable=is_retryable,
                risk_level=risk_level,
                successful_payment_count=successful_payments,
                failed_payment_count=failed_payments,
                customer_lifetime_value=customer_lifetime_value,
                last_successful_payment_at=last_success_at,
                retry_count=retry_count,
                customer_contact_count=contact_count,
                recovery_status="not_started",
                recovered_amount=Decimal("0"),
                created_at=created_at,
                updated_at=created_at,
            )
        )

    return payments


def seed_synthetic_data(
    repository: PayFixRepository,
    count: int = 500,
    seed: int = 20240904,
    config: SyntheticDataConfig | None = None,
    skip_existing: bool = True,
) -> int:
    """Insert ``count`` synthetic payments into ``repository``.

    Args:
        repository: Target repository (its underlying database file).
        count: Number of payments to generate.
        seed: Random seed.
        config: Optional configuration overrides.
        skip_existing: When True (default), skip payments whose IDs already
            exist in the database. This makes the function safely idempotent
            for repeated runs against the same database.

    Returns:
        The number of newly inserted records.
    """
    payments = generate_payments(count=count, seed=seed, config=config)
    inserted = 0
    for payment in payments:
        if skip_existing and repository.get_payment(payment.payment_id) is not None:
            continue
        repository.create_payment(payment)
        inserted += 1
    return inserted


def category_distribution(payments: Iterable[Payment]) -> dict[str, int]:
    """Count payments per failure_category (handy for tests and CLI summaries)."""
    counts: dict[str, int] = {category: 0 for category, _ in FAILURE_CATEGORY_DISTRIBUTION}
    for payment in payments:
        counts[payment.failure_category or "unknown"] += 1
    return counts


def dataset_summary(payments: Iterable[Payment]) -> dict[str, object]:
    """Return a small summary dictionary describing the dataset."""
    payments = list(payments)
    total = len(payments)
    total_amount = sum((p.amount for p in payments), Decimal("0"))
    distribution = category_distribution(payments)
    distribution_pct = {
        category: (count / total) if total else 0.0
        for category, count in distribution.items()
    }
    methods: dict[str, int] = {}
    risk_levels: dict[str, int] = {}
    for payment in payments:
        methods[payment.payment_method] = methods.get(payment.payment_method, 0) + 1
        risk_levels[payment.risk_level] = risk_levels.get(payment.risk_level, 0) + 1
    return {
        "total_payments": total,
        "total_amount": str(total_amount.quantize(Decimal("0.01"))),
        "category_counts": distribution,
        "category_percentages": {k: round(v, 4) for k, v in distribution_pct.items()},
        "method_counts": methods,
        "risk_level_counts": risk_levels,
    }


def seed_to_database_path(
    database_path: str | Path,
    count: int = 500,
    seed: int = 20240904,
    config: SyntheticDataConfig | None = None,
) -> dict[str, object]:
    """Convenience helper for the CLI / API: seed a database file and summarize."""
    repository = PayFixRepository(database_path)
    payments = generate_payments(count=count, seed=seed, config=config)
    inserted = 0
    for payment in payments:
        if repository.get_payment(payment.payment_id) is None:
            repository.create_payment(payment)
            inserted += 1
    summary = dataset_summary(payments)
    summary["inserted"] = inserted
    return summary
