#!/usr/bin/env python3
"""
EURUSD SMC/ICT + Fibonacci — Intraday Backtester
Usage: python run_backtest.py --help
"""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path

from tradalgo.backtest.engine import BacktestConfig, BacktestEngine
from tradalgo.backtest.walk_forward import run_walk_forward, print_wf_summary
from tradalgo.reporting.metrics import compute_metrics
from tradalgo.reporting.trade_log import write_trade_log
from tradalgo.reporting.charts import plot_equity_curve


def _print_metrics(m, initial_equity: float) -> None:
    print("\n" + "=" * 52)
    print("  BACKTEST RESULTS — EURUSD SMC/Fibonacci")
    print("=" * 52)
    print(f"  Total trades     : {m.total_trades}")
    print(f"  Win rate         : {m.win_rate}%")
    print(f"  Avg R:R          : {m.avg_rr}")
    print(f"  Profit factor    : {m.profit_factor}")
    print(f"  Sharpe ratio     : {m.sharpe_ratio}")
    print(f"  Max drawdown     : {m.max_drawdown_pct}%")
    print(f"  Total return     : {m.total_return_pct}%")
    print(f"  Avg P&L / trade  : {m.avg_pnl_pips} pips")
    print(f"  Total P&L USD    : ${m.total_pnl_usd:,.2f}")
    print("=" * 52)


def main() -> None:
    p = argparse.ArgumentParser(description="EURUSD SMC/Fibonacci intraday backtester")
    p.add_argument("--source",  default="yfinance",
                   choices=["yfinance", "histdata_csv", "mt4_csv", "generic_csv"],
                   help="Data source (default: yfinance, max ~2 yrs H1)")
    p.add_argument("--csv",     default=None,  help="Path to CSV file (required for non-yfinance sources)")
    p.add_argument("--start",   default="2023-01-01", help="Backtest start (YYYY-MM-DD)")
    p.add_argument("--end",     default="2025-01-01", help="Backtest end   (YYYY-MM-DD)")
    p.add_argument("--equity",  type=float, default=10_000.0, help="Initial account equity in USD")
    p.add_argument("--risk",    type=float, default=0.01,     help="Risk per trade as fraction (default 0.01 = 1%%)")
    p.add_argument("--spread",         type=float, default=1.5,  help="Spread in pips (default 1.5)")
    p.add_argument("--min-rr",         type=float, default=2.0,  help="Minimum R:R ratio to take a trade (default 2.0)")
    p.add_argument("--impulse",        type=float, default=1.5,  help="OB impulse threshold × ATR (default 1.5)")
    p.add_argument("--ob-lookback",    type=int,   default=800,  help="OB lookback window in bars (default 800)")
    p.add_argument("--max-trades-day", type=int,   default=2,    help="Max trades per day (default 2)")
    p.add_argument("--wf",      action="store_true",          help="Run walk-forward validation instead of single backtest")
    p.add_argument("--is-years",type=int,   default=3,        help="Walk-forward in-sample window in years (default 3)")
    p.add_argument("--oos-years",type=int,  default=1,        help="Walk-forward out-of-sample window in years (default 1)")
    p.add_argument("--output",  default="output",             help="Output directory")
    p.add_argument("--cache",   default="data_cache",         help="Data cache directory")
    p.add_argument("--no-cache",action="store_true",          help="Force re-download / re-parse data")
    args = p.parse_args()

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    Path(args.output).mkdir(parents=True, exist_ok=True)

    config = BacktestConfig(
        source=args.source,
        csv_path=args.csv,
        start_date=args.start,
        end_date=args.end,
        initial_equity=args.equity,
        risk_pct=args.risk,
        spread_pips=args.spread,
        min_rr=args.min_rr,
        impulse_threshold=args.impulse,
        ob_lookback=args.ob_lookback,
        max_trades_per_day=args.max_trades_day,
        output_dir=args.output,
        cache_dir=args.cache,
    )

    # ── Walk-forward mode ──────────────────────────────────────────────────
    if args.wf:
        print(f"Walk-forward validation: {args.start} → {args.end}")
        print(f"  IS={args.is_years}yr  OOS={args.oos_years}yr  step=6m\n")
        windows = run_walk_forward(
            base_config=config,
            full_start=args.start,
            full_end=args.end,
            is_years=args.is_years,
            oos_years=args.oos_years,
        )
        print_wf_summary(windows)

        # Save OOS equity curves as individual charts
        for w in windows:
            if not w.result.equity_curve.empty:
                chart = f"{args.output}/wf_fold{w.fold}_equity_{ts}.png"
                plot_equity_curve(
                    w.result.equity_curve, chart,
                    title=f"WF Fold {w.fold} OOS: {w.oos_start} → {w.oos_end}",
                )
        return

    # ── Single backtest ───────────────────────────────────────────────────
    print(f"Backtest: {args.start} → {args.end}  |  Equity: ${args.equity:,.0f}  |  Source: {args.source}")
    result  = BacktestEngine(config).run()
    metrics = compute_metrics(result.trades, result.equity_curve, args.equity)

    _print_metrics(metrics, args.equity)

    trades_path = f"{args.output}/trades_{ts}.csv"
    chart_path  = f"{args.output}/equity_curve_{ts}.png"

    write_trade_log(result.trades, trades_path)
    plot_equity_curve(result.equity_curve, chart_path)

    print(f"\nTrade log  → {trades_path}")
    print(f"Chart      → {chart_path}")


if __name__ == "__main__":
    main()
