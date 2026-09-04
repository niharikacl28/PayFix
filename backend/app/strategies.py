"""The controlled library of recovery actions PayFix may consider."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyDefinition:
    """A supported action name and its human-readable purpose."""

    name: str
    description: str


STRATEGY_LIBRARY: tuple[StrategyDefinition, ...] = (
    StrategyDefinition("retry_now", "Retry the payment immediately."),
    StrategyDefinition("retry_later", "Schedule a retry after an appropriate delay."),
    StrategyDefinition("payment_link", "Provide the customer a convenient way to complete payment."),
    StrategyDefinition("alternate_payment_method", "Ask the customer to use an available alternative payment method."),
    StrategyDefinition("customer_reminder", "Send a helpful payment reminder."),
    StrategyDefinition("human_escalation", "Route the case to a merchant employee for manual handling."),
    StrategyDefinition("stop", "Take no further automated recovery action."),
)

STRATEGIES_BY_NAME = {strategy.name: strategy for strategy in STRATEGY_LIBRARY}
