"""Clearly synthetic, side-effect-free recovery action executor."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from .guardrails import GuardrailResult
from .models import utc_now
from .recovery_models import SimulatedExecutionResult
from .simulation_models import SimulatedStrategyOutcome


class SimulatedRecoveryExecutor:
    """Produce deterministic simulated outcomes; never contacts a payment network."""

    def execute(self, payment: Mapping[str, Any], outcome: SimulatedStrategyOutcome, guardrail: GuardrailResult) -> SimulatedExecutionResult:
        timestamp = utc_now()
        if not guardrail.allowed:
            return SimulatedExecutionResult(payment["payment_id"], outcome.strategy, False, guardrail, "blocked", Decimal("0"), guardrail.reason, timestamp)
        if outcome.strategy == "stop":
            return SimulatedExecutionResult(payment["payment_id"], outcome.strategy, True, guardrail, "no_action", Decimal("0"), "Stop selected; no recovery action was simulated.", timestamp)
        if outcome.strategy == "human_escalation":
            return SimulatedExecutionResult(payment["payment_id"], outcome.strategy, True, guardrail, "simulated_human_review_queued", Decimal("0"), "Human review was queued in the simulator; no customer or payment system was contacted.", timestamp)
        amount = Decimal(str(payment["amount"]))
        if outcome.success_probability >= Decimal("0.50"):
            return SimulatedExecutionResult(payment["payment_id"], outcome.strategy, True, guardrail, "simulated_recovered", amount, "Synthetic success threshold met; no real money was moved.", timestamp)
        return SimulatedExecutionResult(payment["payment_id"], outcome.strategy, True, guardrail, "simulated_no_recovery", Decimal("0"), "Synthetic success threshold was not met; no real action was taken.", timestamp)
