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
    start_date: str = "2020-01-01"
    end_date: str = "2025-01-01"
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01
    spread_pips: float = 1.5
    max_trades_per_day: int = 2
    eod_close_hour_utc: int = 20
    impulse_bars: int = 3
    impulse_threshold: float = 1.5
    swing_left: int = 5
    swing_right: int = 5
    ob_lookback: int = 800
    fib_lookback_bars: int = 200
    min_rr: float = 2.0
    min_trend_pips: float = 0.0
    output_dir: str = "output"
    cache_dir: str = "data_cache"
    force_download: bool = False


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

        # 1. Load + preprocess
        mkt = load_data(
            source=cfg.source,
            csv_path=cfg.csv_path,
            ticker=cfg.ticker,
            start=cfg.start_date,
            end=cfg.end_date,
            cache_dir=cfg.cache_dir,
            force_download=cfg.force_download,
        )
        h1, d1 = preprocess(mkt.h1, mkt.d1)

        if h1.empty:
            raise ValueError("H1 data is empty after preprocessing. Check date range and source.")

        # 2. D1 indicators (full dataset — safe via backward merge_asof)
        d1_trend = compute_d1_trend(d1, min_ema_gap_pips=cfg.min_trend_pips)

        # 3. H1 indicators (causal by construction)
        h1_atr = atr(h1, 14)
        h1_swings = find_swings(h1, cfg.swing_left, cfg.swing_right)

        # 4. Pre-detect all OBs and FVGs on full H1 (filtered by confirmation in loop)
        all_obs = detect_order_blocks(h1, h1_atr, cfg.impulse_bars, cfg.impulse_threshold)
        all_fvgs = detect_fvgs(h1)

        # 5. Determine start index in h1 (skip warm-up)
        start_ts = pd.Timestamp(cfg.start_date).tz_localize("UTC")
        end_ts = pd.Timestamp(cfg.end_date).tz_localize("UTC")

        h1_in_range = h1[(h1.index >= start_ts) & (h1.index < end_ts)]
        if h1_in_range.empty:
            raise ValueError(f"No H1 data in range {cfg.start_date} → {cfg.end_date}.")

        # Use positional indexer to avoid KeyError on duplicate timestamps
        start_iloc = h1.index.get_loc(h1_in_range.index[0])
        if isinstance(start_iloc, slice):
            start_iloc = start_iloc.start

        # 6. Main bar loop
        equity_curve: dict = {}
        daily_trade_count: dict = {}

        for loc_i in range(start_iloc, len(h1)):
            bar = h1.iloc[loc_i]
            ts: pd.Timestamp = bar.name

            if ts >= end_ts:
                break

            # Update OB mitigation on every bar (both directions)
            for _dir in ("bullish", "bearish"):
                update_mitigation(all_obs, bar, _dir)

            # --- Check exit for open position first ---
            if self.portfolio.open_trade is not None:
                self._check_exit(h1, loc_i)

            # --- EOD forced close ---
            if self.portfolio.open_trade is not None:
                self._check_eod_close(bar, ts)

            # --- Look for new entry ---
            if self.portfolio.open_trade is None:
                date_key = ts.date()
                count_today = daily_trade_count.get(date_key, 0)
                if count_today < cfg.max_trades_per_day:
                    opened = self._look_for_entry(
                        h1, d1_trend, h1_atr, h1_swings, all_obs, all_fvgs, loc_i
                    )
                    if opened:
                        daily_trade_count[date_key] = count_today + 1

            equity_curve[ts] = self.portfolio.equity

        # Close any remaining position at end
        if self.portfolio.open_trade is not None:
            last_bar = h1.iloc[-1]
            self.portfolio.close(last_bar["Close"], last_bar.name, "eod_close")

        return BacktestResult(
            trades=self.portfolio.trades,
            equity_curve=pd.Series(equity_curve),
            config=cfg,
        )

    def _check_exit(self, h1: pd.DataFrame, idx: int) -> None:
        t = self.portfolio.open_trade
        if t is None:
            return
        bar = h1.iloc[idx]
        ts = bar.name

        if t.direction == "bullish":
            # SL hit (conservative: SL before TP if same bar touches both)
            if bar["Low"] <= t.stop_loss:
                self.portfolio.close(t.stop_loss, ts, "sl_hit")
            elif bar["High"] >= t.take_profit:
                self.portfolio.close(t.take_profit, ts, "tp_hit")
        else:
            if bar["High"] >= t.stop_loss:
                self.portfolio.close(t.stop_loss, ts, "sl_hit")
            elif bar["Low"] <= t.take_profit:
                self.portfolio.close(t.take_profit, ts, "tp_hit")

    def _check_eod_close(self, bar: pd.Series, ts: pd.Timestamp) -> None:
        cfg = self.cfg
        is_eod = ts.hour >= cfg.eod_close_hour_utc
        is_friday_night = ts.weekday() == 4 and ts.hour >= 20
        if is_eod or is_friday_night:
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
        """Evaluate entry conditions at the given bar index. Returns True if trade opened."""
        cfg = self.cfg
        bar = h1.iloc[idx]
        ts: pd.Timestamp = bar.name

        # Gate 1: Session filter
        if not is_in_session(ts):
            return False

        # Gate 2: D1 trend (backward merge — no look-ahead)
        trend = get_trend_at_h1_bar(ts, d1_trend)
        if trend == TrendLabel.NEUTRAL:
            return False
        direction = trend.value

        # Gate 3: Active OBs — only visible after their confirmation bar has passed
        active_obs = [
            ob for ob in all_obs
            if ob.confirmation_bar_idx < idx        # confirmed before current bar
            and not ob.is_mitigated
            and ob.direction == direction
            and ob.bar_idx >= max(0, idx - cfg.ob_lookback)
        ]

        if not active_obs:
            return False

        # Gate 4: Fibonacci — only use confirmed swings
        safe_end = idx - cfg.swing_right
        if safe_end <= 0:
            return False
        safe_swings = h1_swings.iloc[:safe_end]
        safe_atr = h1_atr.iloc[:safe_end]

        anchor = find_anchor_swing(safe_swings, safe_atr, direction, cfg.fib_lookback_bars)
        if anchor is None:
            return False
        fib_levels = compute_fib_retracement(*anchor)

        # Gate 5: Confluence (OB ∩ Fibonacci golden zone)
        atr_val = float(h1_atr.iloc[idx])
        if np.isnan(atr_val) or atr_val == 0:
            return False
        confluence = find_confluence(active_obs, fib_levels, direction, atr_val)
        if confluence is None:
            return False

        # Gate 6: Entry trigger candle pattern
        signal = check_entry_trigger(h1, idx, direction, confluence)
        if signal is None:
            return False

        # Gate 7: Risk calculation
        liq_levels = get_liquidity_levels(safe_swings, float(bar["Close"]), direction, atr=atr_val)
        setup = calculate_trade_setup(
            direction=direction,
            entry_price=signal.entry_price,
            ob=confluence.ob,
            atr=atr_val,
            liquidity_levels=liq_levels,
            account_equity=self.portfolio.equity,
            zone_low=confluence.low,
            zone_high=confluence.high,
            min_rr=cfg.min_rr,
            risk_pct=cfg.risk_pct,
            spread_pips=cfg.spread_pips,
        )
        if setup is None:
            return False

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
