"""
core/executor.py

Places orders via broker.ctrader_client.CTraderClient.create_order(), with
a mandatory second guardrail check immediately before execution -- defense
in depth even though core/agent.py already checked. This is the ONLY code
path in the whole project that places a live order, and it is unreachable
from core/agent.py's --dry-run path.
"""
from core import guardrails


class GuardrailViolation(Exception):
    """Raised when the executor's double-check rejects a decision that the
    caller believed was already approved -- this should never happen in
    correct operation and indicates a bug upstream (e.g. stale account/
    daily-stats data passed to the first check)."""


async def execute_order(
    ctrader_client,
    trade_decision: "guardrails.TradeDecision",
    account: "guardrails.AccountState",
    daily_stats: "guardrails.DailyStats",
    guardrail_config: dict,
) -> dict:
    """
    Re-runs guardrails.evaluate_guardrails() with the same inputs the
    caller already validated. If the re-check fails, raises
    GuardrailViolation and does NOT place the order. If approved, calls
    ctrader_client.create_order(...) and returns its result dict.
    """
    outcome = guardrails.evaluate_guardrails(
        trade_decision, account, daily_stats, guardrail_config=guardrail_config
    )
    if outcome.result != guardrails.GuardrailResult.APPROVED:
        raise GuardrailViolation(f"Executor double-check rejected the order: {outcome.reason}")

    return await ctrader_client.create_order(
        symbol=trade_decision.symbol,
        direction=trade_decision.direction,
        size=trade_decision.size,
        stop_loss=trade_decision.stop_loss,
        take_profit=trade_decision.take_profit,
    )
