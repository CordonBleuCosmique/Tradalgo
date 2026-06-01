# Tradalgo — EURUSD SMC/Fibonacci Intraday Backtester

## What this project does
Automatic trading algorithm that:
1. Analyses the EURUSD macro trend on Daily candles (EMA50/200 + market structure HH/HL)
2. Identifies Order Block zones (SMC/ICT) confluent with Fibonacci golden zone (50–61.8%)
3. Enters intraday on confirmation candles (engulfing / pin bar) during London/NY sessions
4. Closes all positions by 20:00 UTC — no overnight exposure
5. Runs as a full backtest (up to 5 years) with anti-look-ahead guarantees and walk-forward validation

## Project structure
```
tradalgo/
├── data/           loader.py (yfinance + CSV), preprocessor.py
├── indicators/     ema.py, atr.py, swing.py
├── smc/            structure.py, order_blocks.py, fvg.py, liquidity.py
├── strategy/       trend_filter.py, fibonacci.py, confluence.py, entry_signals.py, risk.py
│                   trend_w1.py, swing_exits.py, swing_risk.py  (swing/D1 mode)
├── backtest/       engine.py (intraday H1), swing_engine.py (swing D1), portfolio.py, walk_forward.py
└── reporting/      metrics.py, trade_log.py, charts.py
run_backtest.py     CLI entry point  (--mode intraday | swing)
download_histdata.py  auto-download EURUSD M1→H1 from Histdata.com
tests/              test_no_lookahead.py, test_swing_no_lookahead.py
```

## Two strategy modes
- **intraday** (default): H1 SMC/Fibonacci, D1 trend filter, closes by 20:00 UTC. Few trades/yr.
- **swing**: D1 Order Blocks + Fibonacci, W1 (weekly EMA20/50) macro trend, structure-based
  trailing stop, holds days→months (max 180d). Targets large R per trade. No session/EOD close.
  Note: on tiny accounts (€250) with wide D1 stops (150+ pips), the 0.01 min-lot forces
  per-trade risk above the configured risk_pct — micro-lot brokers needed for true 3% sizing.

## Running
```bash
pip install -r requirements.txt

# Quick test (yfinance, ~2 years H1)
python run_backtest.py --start 2023-01-01 --end 2025-01-01 --equity 10000

# 5-year backtest from CSV (Histdata.com or MT4 export)
python run_backtest.py --source histdata_csv --csv path/to/EURUSD_H1.csv \
    --start 2020-01-01 --end 2025-01-01 --equity 10000

# Walk-forward validation (5 years, IS=3yr OOS=1yr)
python run_backtest.py --source histdata_csv --csv path/to/EURUSD_H1.csv \
    --start 2019-01-01 --end 2025-01-01 --wf --equity 10000

# Swing mode (D1 macro, hold weeks/months, €250 account)
python run_backtest.py --mode swing --source histdata_csv --csv path/to/EURUSD_H1.csv \
    --start 2021-01-01 --end 2025-01-01 --equity 250 --risk 0.03
#   swing params: --tp-rr 6.0  --trail-buffer 1.0  --max-hold 180  --no-trailing

# Swing with margin-based sizing (10% margin at 1:30 leverage)
python run_backtest.py --mode swing --source histdata_csv --csv path/to/EURUSD_H1.csv \
    --start 2021-01-01 --end 2025-01-01 --equity 250 \
    --sizing margin --margin-pct 0.10 --leverage 30
#   Sizing modes:
#     risk   : lot sized so SL distance risks risk_pct of equity
#     margin : notional = margin_pct × equity × leverage; lot = notional/(100k×price)
#   NOTE: 0.01 min-lot floor dominates below ~$400 equity (10%×30 → $750 notional
#         < $1,200 = notional of 0.01 lot). Margin sizing only scales above that.

# Anti-look-ahead tests
pip install pytest && pytest tests/ -v
```

## Data sources for 5-year backtest
yfinance H1 is limited to ~730 days. For 5 years of H1 data:
- **Histdata.com** (free): download EURUSD M1 or H1 CSV, pass with `--source histdata_csv`
- **MT4/MT5 export**: export H1 CSV from any broker, pass with `--source mt4_csv`
- Format auto-detected; M1 data is resampled to H1 automatically

## Key architecture decisions
- **No look-ahead bias**: OBs visible only after `confirmation_bar_idx < current_idx`;
  swings accessed as `swing_df.iloc[:idx - right_bars]`; D1 trend via `merge_asof(backward)`
- **Spread model**: 1.5 pip fixed spread subtracted at entry (configurable via `--spread`)
- **SL/TP fill**: conservative — SL fills at SL price even if bar also touches TP (gap model)
- **Position sizing**: 1% equity risk per trade, min lot 0.01
- **Sessions**: London 07–12 UTC, NY 13–18 UTC only

## Parameters (BacktestConfig)
| Param | Default | Description |
|-------|---------|-------------|
| risk_pct | 0.01 | Risk per trade (1%) |
| spread_pips | 1.5 | Fixed spread |
| max_trades_per_day | 2 | Daily cap |
| eod_close_hour_utc | 20 | Force-close hour |
| swing_left/right | 5/5 | Pivot confirmation bars |
| impulse_bars | 3 | OB impulse confirmation candles |
| impulse_threshold | 1.5 | OB impulse strength (× ATR) |
| ob_lookback | 150 | How many bars back to scan for OBs |
| fib_lookback_bars | 50 | Bars to look back for Fibonacci anchor swing |
