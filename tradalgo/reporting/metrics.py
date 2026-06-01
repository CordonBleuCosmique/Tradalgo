from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import numpy as np

from tradalgo.backtest.portfolio import TradeRecord


@dataclass
class BacktestMetrics:
    total_trades: int
    win_rate: float          # %
    avg_rr: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float  # negative value
    total_return_pct: float
    avg_pnl_pips: float
    total_pnl_usd: float


_ZERO = BacktestMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def compute_metrics(
    trades: list[TradeRecord],
    equity_curve: pd.Series,
    initial_equity: float,
) -> BacktestMetrics:
    closed = [t for t in trades if t.exit_reason != "open"]
    if not closed or equity_curve.empty:
        return _ZERO

    wins   = [t for t in closed if t.pnl_usd > 0]
    losses = [t for t in closed if t.pnl_usd <= 0]

    win_rate     = len(wins) / len(closed) * 100
    avg_rr       = float(np.mean([t.r_multiple for t in closed]))
    avg_pnl_pips = float(np.mean([t.pnl_pips for t in closed]))
    total_pnl    = sum(t.pnl_usd for t in closed)

    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss   = abs(sum(t.pnl_usd for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe — annualised from daily equity returns
    daily = equity_curve.resample("1D").last().dropna()
    ret   = daily.pct_change().dropna()
    if len(ret) > 1 and ret.std() > 0:
        sharpe = float((ret.mean() / ret.std()) * np.sqrt(252))
    else:
        sharpe = 0.0

    # Max drawdown
    roll_max = equity_curve.cummax()
    dd       = (equity_curve - roll_max) / roll_max
    max_dd   = float(dd.min()) * 100

    total_return = (equity_curve.iloc[-1] - initial_equity) / initial_equity * 100

    return BacktestMetrics(
        total_trades=len(closed),
        win_rate=round(win_rate, 1),
        avg_rr=round(avg_rr, 2),
        profit_factor=round(profit_factor, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
        total_return_pct=round(total_return, 2),
        avg_pnl_pips=round(avg_pnl_pips, 1),
        total_pnl_usd=round(total_pnl, 2),
    )
