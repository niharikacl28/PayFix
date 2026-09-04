"""Typed results emitted by the synthetic strategy simulator and optimizer."""

from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SimulatedStrategyOutcome:
    payment_id: str
    strategy: str
    eligible: bool
    success_probability: Decimal
    expected_recovered_amount: Decimal
    estimated_customer_friction: str
    estimated_time_to_recovery: str
    rationale: str
    assumptions: list[str]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["success_probability"] = float(self.success_probability)
        result["expected_recovered_amount"] = float(self.expected_recovered_amount)
        return result


@dataclass(frozen=True)
class OptimizationResult:
    payment_id: str
    strategies_evaluated: list[SimulatedStrategyOutcome]
    ranked_strategies: list[SimulatedStrategyOutcome]
    selected_strategy: str
    expected_recovered_amount: Decimal
    selection_reason: str
    simulation_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "payment_id": self.payment_id,
            "strategies_evaluated": [item.to_dict() for item in self.strategies_evaluated],
            "ranked_strategies": [item.to_dict() for item in self.ranked_strategies],
            "selected_strategy": self.selected_strategy,
            "expected_recovered_amount": float(self.expected_recovered_amount),
            "selection_reason": self.selection_reason,
            "simulation_version": self.simulation_version,
        }
