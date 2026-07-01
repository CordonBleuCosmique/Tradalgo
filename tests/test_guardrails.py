"""
tests/test_guardrails.py

Pure unit tests against core.guardrails.evaluate_guardrails. No I/O, no
mocking of broker.ctrader_client needed (guardrails.py has zero I/O).
"""
from core.guardrails import (
    AccountState,
    DailyStats,
    GuardrailResult,
    TradeDecision,
    evaluate_guardrails,
)

GUARDRAIL_CONFIG = {
    "mode": "demo_only",
    "max_position_size_pct": 2,
    "stop_loss_required": True,
    "max_daily_drawdown_pct": 5,
    "max_trades_per_day": 10,
    "allowed_symbols": ["XAUUSD", "EURUSD"],
}


def compliant_decision(**overrides) -> TradeDecision:
    defaults = dict(symbol="XAUUSD", direction="long", size=1.0, stop_loss=1900.0, take_profit=1950.0)
    defaults.update(overrides)
    return TradeDecision(**defaults)


def compliant_account(**overrides) -> AccountState:
    defaults = dict(is_demo=True, balance=10_000.0, equity=10_000.0)
    defaults.update(overrides)
    return AccountState(**defaults)


def compliant_daily_stats(**overrides) -> DailyStats:
    defaults = dict(trades_today=0, drawdown_pct_today=0.0)
    defaults.update(overrides)
    return DailyStats(**defaults)


def test_fully_compliant_decision_is_approved():
    outcome = evaluate_guardrails(
        compliant_decision(), compliant_account(), compliant_daily_stats(),
        guardrail_config=GUARDRAIL_CONFIG,
    )
    assert outcome.result == GuardrailResult.APPROVED
    assert outcome.reason is None


def test_oversized_position_is_rejected():
    decision = compliant_decision(size=5.0)
    outcome = evaluate_guardrails(
        decision, compliant_account(), compliant_daily_stats(), guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.REJECTED
    assert "size" in outcome.reason.lower()


def test_missing_stop_loss_is_rejected():
    decision = compliant_decision(stop_loss=None)
    outcome = evaluate_guardrails(
        decision, compliant_account(), compliant_daily_stats(), guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.REJECTED
    assert "stop loss" in outcome.reason.lower()


def test_daily_drawdown_breach_is_rejected():
    daily_stats = compliant_daily_stats(drawdown_pct_today=-6.0)
    outcome = evaluate_guardrails(
        compliant_decision(), compliant_account(), daily_stats, guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.REJECTED
    assert "drawdown" in outcome.reason.lower()


def test_disallowed_symbol_is_rejected():
    decision = compliant_decision(symbol="GBPUSD")
    outcome = evaluate_guardrails(
        decision, compliant_account(), compliant_daily_stats(), guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.REJECTED
    assert "symbol" in outcome.reason.lower()


def test_non_demo_account_is_rejected():
    account = compliant_account(is_demo=False)
    outcome = evaluate_guardrails(
        compliant_decision(), account, compliant_daily_stats(), guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.REJECTED
    assert "demo" in outcome.reason.lower()


def test_max_trades_per_day_reached_is_rejected():
    daily_stats = compliant_daily_stats(trades_today=10)
    outcome = evaluate_guardrails(
        compliant_decision(), compliant_account(), daily_stats, guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.REJECTED
    assert "max_trades_per_day" in outcome.reason


def test_no_trade_direction_skips_size_and_sl_checks():
    decision = compliant_decision(direction="none", size=999.0, stop_loss=None)
    outcome = evaluate_guardrails(
        decision, compliant_account(), compliant_daily_stats(), guardrail_config=GUARDRAIL_CONFIG
    )
    assert outcome.result == GuardrailResult.APPROVED
