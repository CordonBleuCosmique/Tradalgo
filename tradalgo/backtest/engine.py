from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

from tradalgo.data.loader import load_data
from tradalgo.data.preprocessor import preprocess
from tradalgo.indicators.atr import atr
from tradalgo.indicators.swing import find_swings
from tradalgo.smc.order_blocks import OrderBlock, detect_order_blocks, update_mitigation
from tradalgo.smc.fvg import FairValueGap, detect_fvgs
from tradalgo.smc.liquidity import get_liquidity_levels
from tradalgo.strategy.trend_filter import TrendLabel, compute_d1_trend, get_trend_at_h1_bar
from tradalgo.strategy.fibonacci import find_anchor_swing, compute_fib_retracement
from tradalgo.strategy.confluence import find_confluence
from tradalgo.strategy.entry_signals import check_entry_trigger, is_in_session
from tradalgo.strategy.risk import calculate_trade_setup
from tradalgo.backtest.portfolio import Portfolio, TradeRecord


@dataclass
class BacktestConfig:
    source: str = "yfinance"
    csv_path: Optional[str] = None
    ticker: str = "EURUSD=X"
    start_date: str = "2023-01-01"
    end_date: str = "2025-01-01"
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01
    spread_pips: float = 1.5
    max_trades_per_day: int = 2
    eod_close_hour_utc: int = 20
    swing_left: int = 5
    swing_right: int = 5
    impulse_bars: int = 3
    impulse_threshold: float = 1.5
    ob_lookback: int = 800   # ~5 weeks of H1 bars
    fib_lookback_bars: int = 200
    output_dir: str = "output"
    cache_dir: str = "data_cache"


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    config: BacktestConfig


class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.portfolio = Portfolio(config.initial_equity)

    def run(self) -> BacktestResult:
        cfg = self.cfg

        # ── 1. Load & preprocess ──────────────────────────────────────────
        mkt = load_data(
            source=cfg.source,
            csv_path=cfg.csv_path,
            ticker=cfg.ticker,
            start=cfg.start_date,
            end=cfg.end_date,
            cache_dir=cfg.cache_dir,
        )
        h1, d1 = preprocess(mkt.h1, mkt.d1)

        # ── 2. D1 indicators (computed once on full D1 — safe via merge_asof) ──
        d1_trend = compute_d1_trend(d1)

        # ── 3. H1 indicators (causal by construction) ────────────────────
        h1_atr = atr(h1, 14)
        h1_swings = find_swings(h1, cfg.swing_left, cfg.swing_right)

        # ── 4. Pre-detect all OBs & FVGs (visibility gated by confirmation idx) ──
        all_obs = detect_order_blocks(h1, h1_atr, cfg.impulse_bars, cfg.impulse_threshold)
        all_fvgs = detect_fvgs(h1)

        # ── 5. Find first bar of the official backtest window ─────────────
        start_ts = pd.Timestamp(cfg.start_date, tz="UTC")
        end_ts   = pd.Timestamp(cfg.end_date,   tz="UTC")
        in_range = h1[(h1.index >= start_ts) & (h1.index < end_ts)]
        if in_range.empty:
            return BacktestResult([], pd.Series(dtype=float), cfg)
        start_iloc = h1.index.get_loc(in_range.index[0])

        # ── 6. Bar-by-bar simulation ──────────────────────────────────────
        equity_curve: dict[pd.Timestamp, float] = {}
        daily_trade_count: dict = {}

        for loc_i in range(start_iloc, len(h1)):
            bar = h1.iloc[loc_i]
            ts  = bar.name

            # Exit check first (SL/TP can be hit on any bar)
            if self.portfolio.open_trade is not None:
                self._check_exit(h1, loc_i)

            # EOD forced close (no overnight positions)
            if self.portfolio.open_trade is not None:
                self._check_eod(bar, ts)

            # Entry attempt (only if no position and daily quota not exhausted)
            if self.portfolio.open_trade is None:
                date_key = ts.date()
                if daily_trade_count.get(date_key, 0) < cfg.max_trades_per_day:
                    opened = self._look_for_entry(
                        h1, d1_trend, h1_atr, h1_swings,
                        all_obs, all_fvgs, loc_i,
                    )
                    if opened:
                        daily_trade_count[date_key] = daily_trade_count.get(date_key, 0) + 1

            equity_curve[ts] = self.portfolio.equity

        # Close any position still open at the very last bar
        if self.portfolio.open_trade is not None:
            last = h1.iloc[-1]
            self.portfolio.close(last["Close"], last.name, "eod_close")

        return BacktestResult(
            trades=self.portfolio.trades,
            equity_curve=pd.Series(equity_curve),
            config=cfg,
        )

    # ── helpers ───────────────────────────────────────────────────────────

    def _check_exit(self, h1: pd.DataFrame, idx: int) -> None:
        t   = self.portfolio.open_trade
        bar = h1.iloc[idx]
        ts  = bar.name

        if t.direction == "bullish":
            if bar["Low"] <= t.stop_loss:
                # Conservative: SL fills even if TP was also touched (gap scenario)
                self.portfolio.close(t.stop_loss, ts, "sl_hit")
            elif bar["High"] >= t.take_profit:
                self.portfolio.close(t.take_profit, ts, "tp_hit")
        else:
            if bar["High"] >= t.stop_loss:
                self.portfolio.close(t.stop_loss, ts, "sl_hit")
            elif bar["Low"] <= t.take_profit:
                self.portfolio.close(t.take_profit, ts, "tp_hit")

    def _check_eod(self, bar: pd.Series, ts: pd.Timestamp) -> None:
        cfg = self.cfg
        is_eod      = ts.hour >= cfg.eod_close_hour_utc
        is_friday   = ts.weekday() == 4 and ts.hour >= 20
        if is_eod or is_friday:
            self.portfolio.close(bar["Close"], ts, "eod_close")

    def _look_for_entry(
        self,
        h1: pd.DataFrame,
        d1_trend: pd.Series,
        h1_atr: pd.Series,
        h1_swings: pd.DataFrame,
        all_obs: list[OrderBlock],
        all_fvgs: list[FairValueGap],
        idx: int,
    ) -> bool:
        cfg = self.cfg
        bar = h1.iloc[idx]
        ts  = bar.name

        # Gate 1 – trading session
        if not is_in_session(ts):
            return False

        # Gate 2 – macro trend
        trend = get_trend_at_h1_bar(ts, d1_trend)
        if trend == TrendLabel.NEUTRAL:
            return False
        direction = trend.value  # "bullish" | "bearish"

        # Gate 3 – active Order Blocks
        # Update mitigation for ALL directions on every bar
        for _dir in ("bullish", "bearish"):
            update_mitigation(all_obs, bar, _dir)
        active_obs = [
            ob for ob in all_obs
            if ob.confirmation_bar_idx < idx
            and not ob.is_mitigated
            and ob.direction == direction
            and ob.bar_idx >= max(0, idx - cfg.ob_lookback)
        ]
        if not active_obs:
            return False

        # Gate 4 – Fibonacci (only confirmed swings)
        safe_swing = h1_swings.iloc[: max(0, idx - cfg.swing_right)]
        safe_atr   = h1_atr.iloc[: max(0, idx - cfg.swing_right)]
        anchor = find_anchor_swing(safe_swing, safe_atr, direction, cfg.fib_lookback_bars)
        if anchor is None:
            return False
        fib_levels = compute_fib_retracement(*anchor)

        # Gate 5 – OB ∩ Fibonacci golden zone confluence
        atr_val = float(h1_atr.iloc[idx])
        if np.isnan(atr_val):
            return False
        confluence = find_confluence(active_obs, fib_levels, direction, atr_val)
        if confluence is None:
            return False

        # Gate 6 – entry trigger candle (engulfing / pin bar)
        signal = check_entry_trigger(h1, idx, direction, confluence)
        if signal is None:
            return False

        # Gate 7 – risk/reward
        liq_levels = get_liquidity_levels(safe_swing, bar["Close"], direction)
        setup = calculate_trade_setup(
            direction=direction,
            entry_price=signal.entry_price,
            ob=confluence.ob,
            atr=atr_val,
            liquidity_levels=liq_levels,
            account_equity=self.portfolio.equity,
            zone_low=confluence.low,
            zone_high=confluence.high,
            min_rr=2.0,
            risk_pct=cfg.risk_pct,
            spread_pips=cfg.spread_pips,
        )
        if setup is None:
            return False

        # Open trade
        trade = TradeRecord(
            trade_id=self.portfolio.next_trade_id(),
            direction=direction,
            entry_time=ts,
            exit_time=None,
            entry_price=setup.entry_price,
            exit_price=None,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            lot_size=setup.lot_size,
            exit_reason="open",
            risk_usd=setup.risk_usd,
            rr_ratio=setup.rr_ratio,
        )
        self.portfolio.open(trade)
        return True
