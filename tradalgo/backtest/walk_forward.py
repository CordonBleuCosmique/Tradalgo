from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator
import pandas as pd
import numpy as np

from tradalgo.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from tradalgo.reporting.metrics import compute_metrics, BacktestMetrics


@dataclass
class WFWindow:
    fold: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    result: BacktestResult
    metrics: BacktestMetrics


def _windows(
    full_start: str,
    full_end: str,
    is_years: int,
    oos_years: int,
    step_months: int,
) -> Iterator[tuple[str, str, str, str]]:
    start = pd.Timestamp(full_start)
    end   = pd.Timestamp(full_end)

    current = start
    while True:
        is_end  = current + pd.DateOffset(years=is_years)
        oos_end = is_end  + pd.DateOffset(years=oos_years)
        if oos_end > end:
            break
        yield (
            current.strftime("%Y-%m-%d"),
            is_end.strftime("%Y-%m-%d"),
            is_end.strftime("%Y-%m-%d"),
            oos_end.strftime("%Y-%m-%d"),
        )
        current += pd.DateOffset(months=step_months)


def run_walk_forward(
    base_config: BacktestConfig,
    full_start: str,
    full_end: str,
    is_years: int = 3,
    oos_years: int = 1,
    step_months: int = 6,
) -> list[WFWindow]:
    results: list[WFWindow] = []

    for fold_idx, (is_start, is_end, oos_start, oos_end) in enumerate(
        _windows(full_start, full_end, is_years, oos_years, step_months)
    ):
        print(f"  Fold {fold_idx + 1}: OOS {oos_start} → {oos_end}")

        import dataclasses
        cfg = dataclasses.replace(base_config, start_date=oos_start, end_date=oos_end)
        result  = BacktestEngine(cfg).run()
        metrics = compute_metrics(result.trades, result.equity_curve, cfg.initial_equity)

        results.append(WFWindow(
            fold=fold_idx + 1,
            is_start=is_start,
            is_end=is_end,
            oos_start=oos_start,
            oos_end=oos_end,
            result=result,
            metrics=metrics,
        ))

    return results


def print_wf_summary(windows: list[WFWindow]) -> None:
    sep = "=" * 74
    print(f"\n{sep}")
    print("WALK-FORWARD VALIDATION — OUT-OF-SAMPLE RESULTS")
    print(sep)
    hdr = f"{'Fold':<5} {'OOS Period':<24} {'Trades':<8} {'WR%':<8} {'Sharpe':<8} {'MaxDD%':<9} {'Ret%':<8}"
    print(hdr)
    print("-" * 74)

    for w in windows:
        m = w.metrics
        period = f"{w.oos_start} → {w.oos_end}"
        print(f"{w.fold:<5} {period:<24} {m.total_trades:<8} {m.win_rate:<8.1f} "
              f"{m.sharpe_ratio:<8.2f} {m.max_drawdown_pct:<9.2f} {m.total_return_pct:<8.2f}")

    print(sep)
    if windows:
        avg_wr     = np.mean([w.metrics.win_rate for w in windows])
        avg_sharpe = np.mean([w.metrics.sharpe_ratio for w in windows])
        avg_dd     = np.mean([w.metrics.max_drawdown_pct for w in windows])
        avg_ret    = np.mean([w.metrics.total_return_pct for w in windows])
        total_t    = sum(w.metrics.total_trades for w in windows)
        print(f"AVG OOS : {total_t} trades | WR {avg_wr:.1f}% | "
              f"Sharpe {avg_sharpe:.2f} | MaxDD {avg_dd:.2f}% | Return {avg_ret:.2f}%")
        print(sep)
