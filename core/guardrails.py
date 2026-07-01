"""
core/guardrails.py

Hard-coded, non-negotiable trading guardrails. This module performs NO I/O
(other than `load_guardrail_config`, which is isolated below) so it is
pure and trivially unit-testable, and so both `core/agent.py` and
`core/executor.py` can call `evaluate_guardrails` fresh, immediately
before every single order -- never just once at startup.

The LLM's proposed decision can never bypass these checks: a violation is
a silent REJECTED outcome, logged with a reason, never renegotiated with
the model.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import yaml


class GuardrailResult(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass
class TradeDecision:
    """The LLM's proposed action, already parsed out of its JSON response."""

    symbol: str
    direction: str  # "long" | "short" | "none"
    size: float  # position size, expressed as % of equity per prompt_system.md's schema
    stop_loss: Optional[float]
    take_profit: Optional[float] = None


@dataclass
class AccountState:
    """Account metadata as reported by broker.ctrader_client.get_balance()."""

    is_demo: bool
    balance: float
    equity: float
    currency: str = "USD"


@dataclass
class DailyStats:
    """
    Running daily counters, populated by the caller from decision_log/db.py
    queries -- guardrails.py itself performs no I/O to compute these.
    """

    trades_today: int
    drawdown_pct_today: float  # negative = losing day, e.g. -3.0 means -3%


@dataclass
class GuardrailCheckOutcome:
    result: GuardrailResult
    reason: Optional[str] = None  # populated only when REJECTED


def load_guardrail_config(path: str = "config/guardrails.yaml") -> dict:
    """YAML loader -- the only I/O in this module, isolated from the pure check function."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def evaluate_guardrails(
    decision: TradeDecision,
    account: AccountState,
    daily_stats: DailyStats,
    *,
    guardrail_config: dict,
) -> GuardrailCheckOutcome:
    """
    Pure function. Validates, in order, and short-circuits on the first
    failure (the first failing check is the one reported -- deterministic
    and testable). Returns APPROVED only if every check passes.
    """
    if guardrail_config.get("mode") == "demo_only" and not account.is_demo:
        return GuardrailCheckOutcome(
            GuardrailResult.REJECTED,
            "Account is not in demo mode, but guardrails require mode: demo_only.",
        )

    allowed_symbols = guardrail_config.get("allowed_symbols", [])
    if decision.symbol not in allowed_symbols:
        return GuardrailCheckOutcome(
            GuardrailResult.REJECTED,
            f"Symbol '{decision.symbol}' is not in allowed_symbols {allowed_symbols}.",
        )

    if decision.direction != "none":
        if guardrail_config.get("stop_loss_required") and decision.stop_loss is None:
            return GuardrailCheckOutcome(
                GuardrailResult.REJECTED,
                "stop_loss_required is true but no stop loss was provided.",
            )

        max_size = guardrail_config.get("max_position_size_pct")
        if max_size is not None and decision.size > max_size:
            return GuardrailCheckOutcome(
                GuardrailResult.REJECTED,
                f"Position size {decision.size} exceeds max_position_size_pct {max_size}.",
            )

    max_trades = guardrail_config.get("max_trades_per_day")
    if max_trades is not None and daily_stats.trades_today >= max_trades:
        return GuardrailCheckOutcome(
            GuardrailResult.REJECTED,
            f"max_trades_per_day ({max_trades}) already reached today "
            f"({daily_stats.trades_today} trades).",
        )

    max_drawdown = guardrail_config.get("max_daily_drawdown_pct")
    if max_drawdown is not None and daily_stats.drawdown_pct_today <= -max_drawdown:
        return GuardrailCheckOutcome(
            GuardrailResult.REJECTED,
            f"Daily drawdown {daily_stats.drawdown_pct_today}% has breached "
            f"max_daily_drawdown_pct {max_drawdown}%.",
        )

    return GuardrailCheckOutcome(GuardrailResult.APPROVED, None)
