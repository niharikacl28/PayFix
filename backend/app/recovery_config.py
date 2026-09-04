"""Central defaults for deterministic recovery eligibility checks."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RecoveryConfig:
    """Limits used by the eligibility engine, injectable for merchant policies later."""

    max_automated_retries: int = 2
    max_customer_contacts: int = 2
    max_automated_recovery_amount: Decimal = Decimal("10000")


DEFAULT_RECOVERY_CONFIG = RecoveryConfig()
