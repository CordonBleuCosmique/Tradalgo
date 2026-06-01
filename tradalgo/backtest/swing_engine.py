from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

from tradalgo.data.loader import load_data
from tradalgo.data.preprocessor import preprocess
from tradalgo.indicators.atr import atr
from tradalgo.indicators.swing import find_swings
from tradalgo.smc.order_blocks import OrderBlock, detect_order_blocks, update_mitigation
from tradalgo.smc.liquidity import get_liquidity_levels
from tradalgo.strategy.trend_filter import TrendLabel
from tradalgo.strategy.trend_w1 import resample_weekly, compute_weekly_trend, get_weekly_trend_at
from tradalgo.strategy.fibonacci import find_anchor_swing, compute_fib_retracement
from tradalgo.strategy.confluence import find_confluence
from tradalgo.strategy.swing_risk import calculate_swing_setup
from tradalgo.strategy.swing_exits import update_trailing_stop, check_swing_entry
from tradalgo.backtest.portfolio import Portfolio, TradeRecord


@dataclass
class SwingConfig:
    source: str = "yfinance"
    csv_path: Optional[str] = None
    ticker: str = "EURUSD=X"
    start_date: str = "2020-01-01"
    end_date: str = "2025-01-01"
    initial_equity: float = 250.0
    risk_pct: float = 0.03

    spread_pips: float = 1.5
    atr_period: int = 14
    swing_left: int = 3
    swing_right: int = 3

    # OB detection on D1
    impulse_bars: int = 2
    impulse_threshold: float = 1.0
    ob_lookback: int = 90            # D1 bars (~4 months)
    fib_lookback_bars: int = 60

    # Weekly macro trend
    w1_ema_fast: int = 20
    w1_ema_slow: int = 50

    # Exits
    tp_rr: float = 6.0               # backstop TP in R
    use_trailing: bool = True
    trail_buffer_atr: float = 1.0
    trail_activate_r: float = 1.0    # start trailing after +1R unrealised
    max_hold_days: int = 180         # force-close after 6 months

    output_dir: str = "output"
    cache_dir: str = "data_cache"
    force_download: bool = False


@dataclass
class SwingResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    config: SwingConfig


class SwingEngine:
    def __init__(self, config: SwingConfig):
        self.cfg = config
        self.portfolio = Portfolio(config.initial_equity)
        self._entry_risk_price: float = 0.0   # |entry - initial_sl| for R tracking

    def run(self) -> SwingResult:
        cfg = self.cfg

        mkt = load_data(
            source=cfg.source,
            csv_path=cfg.csv_path,
            ticker=cfg.ticker,
            start=cfg.start_date,
            end=cfg.end_date,
            cache_dir=cfg.cache_dir,
            force_download=cfg.force_download,
        )
        _, d1 = preprocess(mkt.h1, mkt.d1)
        if d1.empty:
            raise ValueError("D1 data is empty after preprocessing.")

        # Weekly macro trend (computed on full set, consumed via backward lookup)
        w1 = resample_weekly(d1)
        w1_trend = compute_weekly_trend(w1, cfg.w1_ema_fast, cfg.w1_ema_slow)

        # D1 indicators (causal)
        d1_atr    = atr(d1, cfg.atr_period)
        d1_swings = find_swings(d1, cfg.swing_left, cfg.swing_right)
        all_obs   = detect_order_blocks(d1, d1_atr, cfg.impulse_bars, cfg.impulse_threshold)

        start_ts = pd.Timestamp(cfg.start_date).tz_localize("UTC")
        end_ts   = pd.Timestamp(cfg.end_date).tz_localize("UTC")
        in_range = d1[(d1.index >= start_ts) & (d1.index < end_ts)]
        if in_range.empty:
            raise ValueError(f"No D1 data in range {cfg.start_date} → {cfg.end_date}.")

        start_iloc = d1.index.get_loc(in_range.index[0])
        if isinstance(start_iloc, slice):
            start_iloc = start_iloc.start

        equity_curve: dict = {}

        for loc_i in range(start_iloc, len(d1)):
            bar = d1.iloc[loc_i]
            ts: pd.Timestamp = bar.name
            if ts >= end_ts:
                break

            for _dir in ("bullish", "bearish"):
                update_mitigation(all_obs, bar, _dir)

            if self.portfolio.open_trade is not None:
                self._manage_open(d1, d1_swings, d1_atr, loc_i)

            if self.portfolio.open_trade is None:
                self._look_for_entry(d1, w1_trend, d1_atr, d1_swings, all_obs, loc_i)

            equity_curve[ts] = self.portfolio.equity

        if self.portfolio.open_trade is not None:
            last = d1.iloc[-1]
            self.portfolio.close(last["Close"], last.name, "end_of_data")

        return SwingResult(
            trades=self.portfolio.trades,
            equity_curve=pd.Series(equity_curve),
            config=cfg,
        )

    # ── Position management ────────────────────────────────────────────────
    def _manage_open(
        self,
        d1: pd.DataFrame,
        d1_swings: pd.DataFrame,
        d1_atr: pd.Series,
        idx: int,
    ) -> None:
        cfg = self.cfg
        t   = self.portfolio.open_trade
        if t is None:
            return
        bar = d1.iloc[idx]
        ts  = bar.name

        # 1. Check current SL/TP against this bar (conservative: SL first)
        if t.direction == "bullish":
            if bar["Low"] <= t.stop_loss:
                self.portfolio.close(t.stop_loss, ts, self._sl_reason(t))
                return
            if bar["High"] >= t.take_profit:
                self.portfolio.close(t.take_profit, ts, "tp_hit")
                return
        else:
            if bar["High"] >= t.stop_loss:
                self.portfolio.close(t.stop_loss, ts, self._sl_reason(t))
                return
            if bar["Low"] <= t.take_profit:
                self.portfolio.close(t.take_profit, ts, "tp_hit")
                return

        # 2. Update trailing stop (only after price has moved +activate_r in favour)
        if cfg.use_trailing and self._entry_risk_price > 0:
            if t.direction == "bullish":
                unrealised_r = (bar["Close"] - t.entry_price) / self._entry_risk_price
            else:
                unrealised_r = (t.entry_price - bar["Close"]) / self._entry_risk_price

            if unrealised_r >= cfg.trail_activate_r:
                atr_val = float(d1_atr.iloc[idx])
                new_sl = update_trailing_stop(
                    direction=t.direction,
                    current_sl=t.stop_loss,
                    swing_df=d1_swings,
                    atr_val=atr_val,
                    idx=idx,
                    swing_right=cfg.swing_right,
                    trail_buffer_atr=cfg.trail_buffer_atr,
                )
                t.stop_loss = new_sl

        # 3. Max hold time
        if t.entry_time is not None:
            held_days = (ts - t.entry_time).days
            if held_days >= cfg.max_hold_days:
                self.portfolio.close(bar["Close"], ts, "max_hold")

    @staticmethod
    def _sl_reason(t: TradeRecord) -> str:
        """Distinguish a trailing-stop exit (in profit) from the initial stop."""
        if t.direction == "bullish":
            return "trail_stop" if t.stop_loss >= t.entry_price else "sl_hit"
        return "trail_stop" if t.stop_loss <= t.entry_price else "sl_hit"

    # ── Entry ──────────────────────────────────────────────────────────────
    def _look_for_entry(
        self,
        d1: pd.DataFrame,
        w1_trend: pd.Series,
        d1_atr: pd.Series,
        d1_swings: pd.DataFrame,
        all_obs: list[OrderBlock],
        idx: int,
    ) -> bool:
        cfg = self.cfg
        bar = d1.iloc[idx]
        ts  = bar.name

        # Gate 1: weekly macro trend (backward lookup — no look-ahead)
        trend = get_weekly_trend_at(ts, w1_trend)
        if trend == TrendLabel.NEUTRAL:
            return False
        direction = trend.value

        # Gate 2: active D1 OBs in trend direction
        active_obs = [
            ob for ob in all_obs
            if ob.confirmation_bar_idx < idx
            and not ob.is_mitigated
            and ob.direction == direction
            and ob.bar_idx >= max(0, idx - cfg.ob_lookback)
        ]
        if not active_obs:
            return False

        # Gate 3: Fibonacci anchor from confirmed swings only
        safe_end = idx - cfg.swing_right
        if safe_end <= 0:
            return False
        safe_swings = d1_swings.iloc[:safe_end]
        safe_atr    = d1_atr.iloc[:safe_end]

        anchor = find_anchor_swing(safe_swings, safe_atr, direction, cfg.fib_lookback_bars)
        if anchor is None:
            return False
        fib_levels = compute_fib_retracement(*anchor)

        atr_val = float(d1_atr.iloc[idx])
        if np.isnan(atr_val) or atr_val == 0:
            return False

        # Gate 4: OB ∩ Fibonacci golden zone
        confluence = find_confluence(active_obs, fib_levels, direction, atr_val)
        if confluence is None:
            return False

        # Gate 5: swing pullback trigger (zone tap that held)
        signal = check_swing_entry(d1, idx, direction, confluence, atr_val)
        if signal is None:
            return False

        # Gate 6: risk setup
        liq_levels = get_liquidity_levels(safe_swings, float(bar["Close"]), direction, atr=atr_val)
        setup = calculate_swing_setup(
            direction=direction,
            entry_price=signal.entry_price,
            atr=atr_val,
            zone_low=confluence.low,
            zone_high=confluence.high,
            liquidity_levels=liq_levels,
            account_equity=self.portfolio.equity,
            tp_rr=cfg.tp_rr,
            risk_pct=cfg.risk_pct,
            spread_pips=cfg.spread_pips,
        )
        if setup is None:
            return False

        self._entry_risk_price = abs(setup.entry_price - setup.stop_loss)

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
