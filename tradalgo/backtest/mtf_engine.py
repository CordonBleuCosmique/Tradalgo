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
from tradalgo.strategy.trend_filter import TrendLabel, compute_d1_trend, get_trend_at_h1_bar
from tradalgo.strategy.trend_w1 import resample_weekly, compute_weekly_trend, get_weekly_trend_at
from tradalgo.strategy.fibonacci import find_anchor_swing, compute_fib_retracement
from tradalgo.strategy.confluence import find_confluence
from tradalgo.strategy.entry_signals import check_entry_trigger, is_in_session
from tradalgo.strategy.risk import calculate_trade_setup
from tradalgo.backtest.portfolio import Portfolio, TradeRecord


_OHLCV_AGG = {
    "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
}


@dataclass
class MTFConfig:
    source: str = "yfinance"
    csv_path: Optional[str] = None
    ticker: str = "EURUSD=X"
    start_date: str = "2023-01-01"
    end_date: str = "2025-01-01"
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01
    spread_pips: float = 1.5
    min_rr: float = 2.0
    min_trend_pips: float = 0.0

    # ATR / swing periods (in bars — applied per TF)
    atr_period: int = 14
    swing_left: int = 3
    swing_right: int = 3

    # W1 macro filter
    w1_ema_fast: int = 20
    w1_ema_slow: int = 50

    # H4 signal layer
    h4_impulse_bars: int = 2
    h4_impulse_threshold: float = 1.5
    h4_ob_lookback: int = 60    # H4 bars  (~10 days)
    h4_fib_lookback: int = 60   # H4 bars for Fibonacci anchor

    # H1 execution layer
    max_trades_per_day: int = 2
    eod_close_hour_utc: int = 20

    output_dir: str = "output"
    cache_dir: str = "data_cache"
    force_download: bool = False


@dataclass
class MTFResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    config: MTFConfig


class MTFEngine:
    """
    Multi-timeframe engine (W1/D1 → H4 signal → H1 execution).

    Timeframe cascade:
      W1 / D1 : macro bias — direction gate only
      H4      : Order Block + Fibonacci golden-zone confluence (signal TF)
      H1      : entry trigger — engulfing / pin bar / momentum close inside H4 zone

    No look-ahead:
      • H4 bars consumed only after they fully close (open_time + 4 h ≤ current H1 ts)
      • H4 OB mitigation updated bar-by-bar as new H4 closes arrive
      • D1 trend via backward merge (get_trend_at_h1_bar)
      • W1 bias via backward lookup requiring full week closed
    """

    def __init__(self, config: MTFConfig):
        self.cfg = config
        self.portfolio = Portfolio(config.initial_equity)

    def run(self) -> MTFResult:
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
        h1, d1 = preprocess(mkt.h1, mkt.d1)
        if h1.empty:
            raise ValueError("H1 data is empty after preprocessing.")

        # H4 data — resampled from H1 (no network call needed)
        h4 = h1.resample("4h").agg(
            {k: v for k, v in _OHLCV_AGG.items() if k in h1.columns}
        ).dropna(subset=["Open"])

        # Trend layers (computed on full datasets — causal via backward lookup)
        w1       = resample_weekly(d1)
        w1_trend = compute_weekly_trend(w1, cfg.w1_ema_fast, cfg.w1_ema_slow)
        d1_trend = compute_d1_trend(d1, min_ema_gap_pips=cfg.min_trend_pips)

        # H4 indicators
        h4_atr     = atr(h4, cfg.atr_period)
        h4_swings  = find_swings(h4, cfg.swing_left, cfg.swing_right)
        all_h4_obs = detect_order_blocks(h4, h4_atr, cfg.h4_impulse_bars, cfg.h4_impulse_threshold)

        # H1 indicators
        h1_atr    = atr(h1, cfg.atr_period)
        h1_swings = find_swings(h1, cfg.swing_left, cfg.swing_right)

        # Precompute no-look-ahead mapping: H1 bar i → last CLOSED H4 bar index
        # H4 bar with open_time T is closed when current_time >= T + 4 h.
        h4_close_ns = (h4.index + pd.Timedelta(hours=4)).asi8
        h1_ns       = h1.index.asi8
        h1_to_h4    = np.searchsorted(h4_close_ns, h1_ns, side="right") - 1

        start_ts = pd.Timestamp(cfg.start_date).tz_localize("UTC")
        end_ts   = pd.Timestamp(cfg.end_date).tz_localize("UTC")
        in_range = h1[(h1.index >= start_ts) & (h1.index < end_ts)]
        if in_range.empty:
            raise ValueError(f"No H1 data in range {cfg.start_date} → {cfg.end_date}.")

        start_iloc = h1.index.get_loc(in_range.index[0])
        if isinstance(start_iloc, slice):
            start_iloc = start_iloc.start

        equity_curve: dict      = {}
        daily_trade_count: dict = {}
        last_h4_mitigated: int  = -1

        for loc_i in range(start_iloc, len(h1)):
            bar = h1.iloc[loc_i]
            ts: pd.Timestamp = bar.name
            if ts >= end_ts:
                break

            h4_idx = int(h1_to_h4[loc_i])

            # Update H4 OB mitigation for each newly closed H4 bar
            if h4_idx > last_h4_mitigated and h4_idx >= 0:
                for j in range(last_h4_mitigated + 1, h4_idx + 1):
                    h4_bar = h4.iloc[j]
                    for _dir in ("bullish", "bearish"):
                        update_mitigation(all_h4_obs, h4_bar, _dir)
                last_h4_mitigated = h4_idx

            if self.portfolio.open_trade is not None:
                self._check_exit(h1, loc_i)
            if self.portfolio.open_trade is not None:
                self._check_eod_close(bar, ts)

            if self.portfolio.open_trade is None:
                date_key = ts.date()
                count = daily_trade_count.get(date_key, 0)
                if count < cfg.max_trades_per_day:
                    opened = self._look_for_entry(
                        h1, h4_swings, h4_atr, h1_atr, h1_swings,
                        w1_trend, d1_trend, all_h4_obs, loc_i, h4_idx,
                    )
                    if opened:
                        daily_trade_count[date_key] = count + 1

            equity_curve[ts] = self.portfolio.equity

        if self.portfolio.open_trade is not None:
            last = h1.iloc[-1]
            self.portfolio.close(last["Close"], last.name, "end_of_data")

        return MTFResult(
            trades=self.portfolio.trades,
            equity_curve=pd.Series(equity_curve),
            config=cfg,
        )

    # ── Position management ────────────────────────────────────────────────

    def _check_exit(self, h1: pd.DataFrame, idx: int) -> None:
        t = self.portfolio.open_trade
        if t is None:
            return
        bar = h1.iloc[idx]
        ts  = bar.name
        if t.direction == "bullish":
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
        is_friday_close = ts.weekday() == 4 and ts.hour >= 20
        if is_friday_close or ts.hour >= self.cfg.eod_close_hour_utc:
            self.portfolio.close(bar["Close"], ts, "eod_close")

    # ── Entry ──────────────────────────────────────────────────────────────

    def _look_for_entry(
        self,
        h1: pd.DataFrame,
        h4_swings: pd.DataFrame,
        h4_atr: pd.Series,
        h1_atr: pd.Series,
        h1_swings: pd.DataFrame,
        w1_trend: pd.Series,
        d1_trend: pd.Series,
        all_h4_obs: list[OrderBlock],
        h1_idx: int,
        h4_idx: int,
    ) -> bool:
        cfg = self.cfg
        bar = h1.iloc[h1_idx]
        ts  = bar.name

        # Gate 1: session (London + NY)
        if not is_in_session(ts):
            return False

        # Gate 2: D1 trend
        d1_bias = get_trend_at_h1_bar(ts, d1_trend)
        if d1_bias == TrendLabel.NEUTRAL:
            return False

        # Gate 3: W1 must not contradict D1 (neutral W1 is allowed)
        w1_bias = get_weekly_trend_at(ts, w1_trend)
        if w1_bias != TrendLabel.NEUTRAL and w1_bias != d1_bias:
            return False

        direction = d1_bias.value

        # Gate 4: active H4 OBs in trend direction
        if h4_idx < 0:
            return False
        active_h4_obs = [
            ob for ob in all_h4_obs
            if ob.confirmation_bar_idx <= h4_idx
            and not ob.is_mitigated
            and ob.direction == direction
            and ob.bar_idx >= max(0, h4_idx - cfg.h4_ob_lookback)
        ]
        if not active_h4_obs:
            return False

        # Gate 5: H4 Fibonacci golden zone
        safe_h4_end = h4_idx - cfg.swing_right
        if safe_h4_end <= 0:
            return False
        anchor = find_anchor_swing(
            h4_swings.iloc[:safe_h4_end],
            h4_atr.iloc[:safe_h4_end],
            direction,
            cfg.h4_fib_lookback,
        )
        if anchor is None:
            return False
        fib_levels = compute_fib_retracement(*anchor)

        h4_atr_val = float(h4_atr.iloc[h4_idx])
        if np.isnan(h4_atr_val) or h4_atr_val == 0:
            return False

        # Gate 6: H4 OB ∩ H4 Fibonacci confluence
        confluence = find_confluence(active_h4_obs, fib_levels, direction, h4_atr_val)
        if confluence is None:
            return False

        # Gate 7: H1 entry trigger inside the H4 zone
        signal = check_entry_trigger(h1, h1_idx, direction, confluence)
        if signal is None:
            return False

        # Gate 8: risk setup
        h1_atr_val = float(h1_atr.iloc[h1_idx])
        if np.isnan(h1_atr_val) or h1_atr_val == 0:
            return False

        safe_h1_end = max(0, h1_idx - cfg.swing_right)
        liq_levels = get_liquidity_levels(
            h1_swings.iloc[:safe_h1_end],
            float(bar["Close"]),
            direction,
            atr=h1_atr_val,
        )
        setup = calculate_trade_setup(
            direction=direction,
            entry_price=signal.entry_price,
            ob=confluence.ob,
            atr=h1_atr_val,
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
