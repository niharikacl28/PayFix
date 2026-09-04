"""Tests for the synthetic payment-data generator."""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.models import Payment
from app.synthetic_data import (
    FAILURE_CATEGORY_DISTRIBUTION,
    SyntheticDataConfig,
    VALID_FAILURE_CATEGORIES,
    VALID_PAYMENT_METHODS,
    category_distribution,
    dataset_summary,
    generate_payments,
    seed_synthetic_data,
)
from app.repositories import PayFixRepository


class TestSyntheticDataGenerator(unittest.TestCase):
    """Tests for ``generate_payments`` and ``seed_synthetic_data``."""

    def setUp(self) -> None:
        self.database_path = Path(__file__).parents[1] / f"test-synth-{uuid4().hex}.db"

    def tearDown(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()

    # ------------------------------------------------------------------
    # Count + size
    # ------------------------------------------------------------------

    def test_generates_requested_number_of_payments(self) -> None:
        """Generator must produce exactly ``count`` records."""
        payments = generate_payments(count=123, seed=1)
        self.assertEqual(len(payments), 123)

    def test_default_count_is_500(self) -> None:
        """Default ``count`` matches the milestone spec (500)."""
        payments = generate_payments(seed=2)
        self.assertEqual(len(payments), 500)

    def test_zero_count_returns_empty_list(self) -> None:
        """Edge case: count=0 yields no payments without errors."""
        self.assertEqual(generate_payments(count=0, seed=3), [])

    def test_negative_count_raises(self) -> None:
        """Edge case: count<0 raises ValueError."""
        with self.assertRaises(ValueError):
            generate_payments(count=-1, seed=4)

    # ------------------------------------------------------------------
    # Uniqueness
    # ------------------------------------------------------------------

    def test_payment_ids_are_unique(self) -> None:
        """Every generated payment must have a unique ID."""
        payments = generate_payments(count=500, seed=5)
        ids = [payment.payment_id for payment in payments]
        self.assertEqual(len(ids), len(set(ids)))

    def test_payment_ids_are_clearly_synthetic(self) -> None:
        """IDs must start with the synthetic prefix ``pay_syn_``."""
        payments = generate_payments(count=50, seed=6)
        for payment in payments:
            self.assertTrue(
                payment.payment_id.startswith("pay_syn_"),
                msg=f"Payment ID is not synthetic: {payment.payment_id}",
            )

    def test_customer_ids_are_unique(self) -> None:
        """Customer IDs must also be unique within a generated batch."""
        payments = generate_payments(count=500, seed=7)
        ids = [payment.customer_id for payment in payments]
        self.assertEqual(len(ids), len(set(ids)))

    # ------------------------------------------------------------------
    # Validity
    # ------------------------------------------------------------------

    def test_amounts_are_positive_decimals(self) -> None:
        """All amounts must be > 0 and stored as Decimal."""
        payments = generate_payments(count=200, seed=8)
        for payment in payments:
            self.assertIsInstance(payment.amount, Decimal)
            self.assertGreater(payment.amount, Decimal("0"))

    def test_amounts_within_configured_bounds(self) -> None:
        """No amount may be below 1 INR or above 999999 INR (sanity bound)."""
        payments = generate_payments(count=200, seed=9)
        for payment in payments:
            self.assertGreaterEqual(payment.amount, Decimal("1"))
            self.assertLessEqual(payment.amount, Decimal("999999"))

    def test_failure_categories_are_valid(self) -> None:
        """Every payment's failure_category must be in the valid set."""
        payments = generate_payments(count=200, seed=10)
        for payment in payments:
            self.assertIn(payment.failure_category, VALID_FAILURE_CATEGORIES)

    def test_payment_methods_are_valid(self) -> None:
        """Every primary payment_method must be a known method."""
        payments = generate_payments(count=200, seed=11)
        for payment in payments:
            self.assertIn(payment.payment_method, VALID_PAYMENT_METHODS)

    def test_available_methods_include_primary(self) -> None:
        """``available_payment_methods`` must include the primary ``payment_method``."""
        payments = generate_payments(count=100, seed=12)
        for payment in payments:
            self.assertIn(payment.payment_method, payment.available_payment_methods)

    def test_every_payment_is_failed(self) -> None:
        """All generated payments must have ``payment_status == 'failed'``."""
        payments = generate_payments(count=200, seed=13)
        for payment in payments:
            self.assertEqual(payment.payment_status, "failed")

    def test_history_counts_are_non_negative(self) -> None:
        """successful_payment_count, failed_payment_count, retry/contact must be >= 0."""
        payments = generate_payments(count=200, seed=14)
        for payment in payments:
            self.assertGreaterEqual(payment.successful_payment_count, 0)
            self.assertGreaterEqual(payment.failed_payment_count, 0)
            self.assertGreaterEqual(payment.retry_count, 0)
            self.assertGreaterEqual(payment.customer_contact_count, 0)

    def test_created_at_is_iso8601_string(self) -> None:
        """Created timestamps must be ISO-8601 strings parseable by datetime."""
        from datetime import datetime
        payments = generate_payments(count=20, seed=15)
        for payment in payments:
            # Should not raise.
            datetime.fromisoformat(payment.created_at)

    def test_risk_levels_are_known(self) -> None:
        """risk_level must be one of the known bands."""
        payments = generate_payments(count=200, seed=16)
        for payment in payments:
            self.assertIn(payment.risk_level, {"low", "medium", "high"})

    # ------------------------------------------------------------------
    # Determinism
    # ------------------------------------------------------------------

    def test_same_seed_produces_identical_payments(self) -> None:
        """Two runs with the same seed must be byte-identical."""
        first = generate_payments(count=100, seed=42)
        second = generate_payments(count=100, seed=42)
        self.assertEqual(len(first), len(second))
        for a, b in zip(first, second):
            self.assertEqual(a.payment_id, b.payment_id)
            self.assertEqual(a.customer_id, b.customer_id)
            self.assertEqual(a.amount, b.amount)
            self.assertEqual(a.failure_category, b.failure_category)
            self.assertEqual(a.payment_method, b.payment_method)
            self.assertEqual(a.available_payment_methods, b.available_payment_methods)
            self.assertEqual(a.successful_payment_count, b.successful_payment_count)
            self.assertEqual(a.failed_payment_count, b.failed_payment_count)
            self.assertEqual(a.retry_count, b.retry_count)
            self.assertEqual(a.customer_contact_count, b.customer_contact_count)
            self.assertEqual(a.risk_level, b.risk_level)
            self.assertEqual(a.is_retryable, b.is_retryable)
            self.assertEqual(a.created_at, b.created_at)

    def test_different_seeds_produce_different_payments(self) -> None:
        """Different seeds must produce at least one different payment."""
        first = generate_payments(count=100, seed=1)
        second = generate_payments(count=100, seed=2)
        differing = sum(
            1 for a, b in zip(first, second) if a.payment_id != b.payment_id
        )
        self.assertGreater(differing, 0)

    # ------------------------------------------------------------------
    # Distribution
    # ------------------------------------------------------------------

    def test_failure_category_distribution_is_close_to_target(self) -> None:
        """Generated categories should be close to the configured percentages.

        We allow a generous absolute tolerance (5 percentage points) to keep
        this robust at 500 samples while still catching serious skew.
        """
        payments = generate_payments(count=500, seed=17)
        counts = category_distribution(payments)
        total = sum(counts.values())
        self.assertEqual(total, 500)
        for category, target_weight in FAILURE_CATEGORY_DISTRIBUTION:
            actual_pct = counts[category] / total
            self.assertAlmostEqual(
                actual_pct,
                target_weight,
                delta=0.05,
                msg=(
                    f"Category {category!r} expected ~{target_weight:.0%}, "
                    f"got {actual_pct:.2%}"
                ),
            )

    def test_dataset_summary_keys_present(self) -> None:
        """``dataset_summary`` must return the documented keys."""
        summary = dataset_summary(generate_payments(count=50, seed=18))
        for key in (
            "total_payments",
            "total_amount",
            "category_counts",
            "category_percentages",
            "method_counts",
            "risk_level_counts",
        ):
            self.assertIn(key, summary)
        self.assertEqual(summary["total_payments"], 50)

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def test_seed_synthetic_data_inserts_records(self) -> None:
        """``seed_synthetic_data`` must persist exactly the requested count."""
        repository = PayFixRepository(self.database_path)
        inserted = seed_synthetic_data(
            repository, count=50, seed=19, config=SyntheticDataConfig(seed=19)
        )
        self.assertEqual(inserted, 50)
        listed = repository.list_payments()
        # Six existing demo records may also be present; assert our 50 are present.
        synth_ids = {f"pay_syn_{i:05d}_" for i in range(50)}
        matched = [p for p in listed if any(p["payment_id"].startswith(prefix) for prefix in synth_ids)]
        self.assertGreaterEqual(len(matched), 50)

    def test_seed_synthetic_data_is_idempotent(self) -> None:
        """Repeated seeding with the same seed does not duplicate records."""
        repository = PayFixRepository(self.database_path)
        first = seed_synthetic_data(
            repository, count=20, seed=20, config=SyntheticDataConfig(seed=20)
        )
        second = seed_synthetic_data(
            repository, count=20, seed=20, config=SyntheticDataConfig(seed=20)
        )
        self.assertEqual(first, 20)
        self.assertEqual(second, 0)
        all_ids = {p["payment_id"] for p in repository.list_payments()}
        self.assertEqual(len(all_ids), 20)

    def test_seeded_payments_round_trip_through_repository(self) -> None:
        """Generated payments must be readable back through the repository."""
        repository = PayFixRepository(self.database_path)
        seed_synthetic_data(
            repository, count=10, seed=21, config=SyntheticDataConfig(seed=21)
        )
        for payment in generate_payments(count=10, seed=21):
            row = repository.get_payment(payment.payment_id)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["payment_status"], "failed")
            self.assertEqual(row["failure_category"], payment.failure_category)
            self.assertEqual(Decimal(str(row["amount"])), payment.amount)
            self.assertEqual(row["payment_method"], payment.payment_method)
            self.assertEqual(
                sorted(row["available_payment_methods"]),
                sorted(payment.available_payment_methods),
            )

    def test_existing_demo_payments_remain_intact_after_seeding(self) -> None:
        """Seeding must NOT remove or rename the original demo payments."""
        repository = PayFixRepository(self.database_path)
        # Insert the canonical demo payments first.
        from app.demo_data import DEMO_PAYMENTS
        for demo in DEMO_PAYMENTS:
            repository.create_payment(demo)
        seed_synthetic_data(
            repository, count=30, seed=22, config=SyntheticDataConfig(seed=22)
        )
        listed = repository.list_payments()
        demo_ids = {demo.payment_id for demo in DEMO_PAYMENTS}
        present_demo = {p["payment_id"] for p in listed} & demo_ids
        self.assertEqual(present_demo, demo_ids)


if __name__ == "__main__":
    unittest.main()
