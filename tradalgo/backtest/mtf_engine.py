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
    h4_ob_lookback: int = 200   # H4 bars (~33 days — equivalent to 800 H1 bars)
    h4_fib_lookback: int = 120  # H4 bars for Fibonacci anchor (~20 days)

    # Execution layer (M15 when available, else H1 fallback)
    max_trades_per_day: int = 3
    eod_close_hour_utc: int = 20

    output_dir: str = "output"
    cache_dir: str = "data_cache"
    force_download: bool = False


@dataclass
class MTFResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    config: MTFConfig
    exec_tf: str   # "M15" or "H1" — which TF was used for execution


class MTFEngine:
    """
    Multi-timeframe engine.

    Full cascade (when M1 CSV is provided):
      W1 / D1  → macro bias
      H4       → Order Block + Fibonacci confluence (signal TF)
      M15      → entry trigger — engulfing / pin / momentum close inside H4 zone

    Fallback (with H1 CSV):
      W1 / D1 → H4 → H1 entry trigger (same logic, coarser granularity)

    No look-ahead guarantees:
      • H4 bars consumed only after open_time + 4 h ≤ current bar timestamp
      • H4 OB mitigation updated lazily bar-by-bar
      • D1 / W1 trend via backward timestamp lookup
      • Entry SL sized on H1 ATR (robust vs M15 noise)
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

        # Execution TF: M15 (from M1 source) or H1 fallback
        if mkt.m15 is not None and not mkt.m15.empty:
            exec_df = mkt.m15
            exec_tf = "M15"
            exec_bar_minutes = 15
        else:
            exec_df = h1
            exec_tf = "H1"
            exec_bar_minutes = 60

        # H4 — resampled from H1
        h4 = h1.resample("4h").agg(
            {k: v for k, v in _OHLCV_AGG.items() if k in h1.columns}
        ).dropna(subset=["Open"])

        # Trend layers
        w1       = resample_weekly(d1)
        w1_trend = compute_weekly_trend(w1, cfg.w1_ema_fast, cfg.w1_ema_slow)
        d1_trend = compute_d1_trend(d1, min_ema_gap_pips=cfg.min_trend_pips)

        # H4 indicators
        h4_atr     = atr(h4, cfg.atr_period)
        h4_swings  = find_swings(h4, cfg.swing_left, cfg.swing_right)
        all_h4_obs = detect_order_blocks(h4, h4_atr, cfg.h4_impulse_bars, cfg.h4_impulse_threshold)

        # H1 indicators — always used for SL sizing and liquidity levels
        h1_atr    = atr(h1, cfg.atr_period)
        h1_swings = find_swings(h1, cfg.swing_left, cfg.swing_right)

        # ── No-look-ahead index mappings ──────────────────────────────────
        # exec bar i → last CLOSED H4 bar index (H4 bar closes at open_time + 4h)
        h4_close_ns   = (h4.index + pd.Timedelta(hours=4)).asi8
        exec_to_h4    = np.searchsorted(h4_close_ns, exec_df.index.asi8, side="right") - 1

        # exec bar i → last CLOSED H1 bar index (needed when exec_tf == "M15")
        h1_close_ns   = (h1.index + pd.Timedelta(hours=1)).asi8
        exec_to_h1    = np.searchsorted(h1_close_ns, exec_df.index.asi8, side="right") - 1

        start_ts = pd.Timestamp(cfg.start_date).tz_localize("UTC")
        end_ts   = pd.Timestamp(cfg.end_date).tz_localize("UTC")
        in_range = exec_df[(exec_df.index >= start_ts) & (exec_df.index < end_ts)]
        if in_range.empty:
            raise ValueError(f"No {exec_tf} data in range {cfg.start_date} → {cfg.end_date}.")

        start_iloc = exec_df.index.get_loc(in_range.index[0])
        if isinstance(start_iloc, slice):
            start_iloc = start_iloc.start

        equity_curve: dict      = {}
        daily_trade_count: dict = {}
        last_h4_mitigated: int  = -1

        for loc_i in range(start_iloc, len(exec_df)):
            bar = exec_df.iloc[loc_i]
            ts: pd.Timestamp = bar.name
            if ts >= end_ts:
                break

            h4_idx = int(exec_to_h4[loc_i])
            h1_idx = int(exec_to_h1[loc_i])

            # Lazily update H4 OB mitigation on each newly closed H4 bar
            if h4_idx > last_h4_mitigated and h4_idx >= 0:
                for j in range(last_h4_mitigated + 1, h4_idx + 1):
                    h4_bar = h4.iloc[j]
                    for _dir in ("bullish", "bearish"):
                        update_mitigation(all_h4_obs, h4_bar, _dir, j)
                last_h4_mitigated = h4_idx

            if self.portfolio.open_trade is not None:
                self._check_exit(exec_df, loc_i)
            if self.portfolio.open_trade is not None:
                self._check_eod_close(bar, ts)

            if self.portfolio.open_trade is None:
                date_key = ts.date()
                count = daily_trade_count.get(date_key, 0)
                if count < cfg.max_trades_per_day:
                    opened = self._look_for_entry(
                        exec_df, h4_swings, h4_atr, h1_atr, h1_swings,
                        w1_trend, d1_trend, all_h4_obs,
                        loc_i, h4_idx, h1_idx,
                    )
                    if opened:
                        daily_trade_count[date_key] = count + 1

            # Record equity at H1 granularity to keep curve readable
            if exec_tf == "M15" and ts.minute != 0:
                pass
            else:
                equity_curve[ts] = self.portfolio.equity

        if self.portfolio.open_trade is not None:
            last = exec_df.iloc[-1]
            self.portfolio.close(last["Close"], last.name, "end_of_data")

        return MTFResult(
            trades=self.portfolio.trades,
            equity_curve=pd.Series(equity_curve),
            config=cfg,
            exec_tf=exec_tf,
        )

    # ── Position management ────────────────────────────────────────────────

    def _check_exit(self, exec_df: pd.DataFrame, idx: int) -> None:
        t = self.portfolio.open_trade
        if t is None:
            return
        bar = exec_df.iloc[idx]
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
        # MTF strategy holds across days — only close on Friday to avoid weekend gap risk.
        if ts.weekday() == 4 and ts.hour >= self.cfg.eod_close_hour_utc:
            self.portfolio.close(bar["Close"], ts, "friday_close")

    # ── Entry ──────────────────────────────────────────────────────────────

    def _look_for_entry(
        self,
        exec_df: pd.DataFrame,
        h4_swings: pd.DataFrame,
        h4_atr: pd.Series,
        h1_atr: pd.Series,
        h1_swings: pd.DataFrame,
        w1_trend: pd.Series,
        d1_trend: pd.Series,
        all_h4_obs: list[OrderBlock],
        exec_idx: int,
        h4_idx: int,
        h1_idx: int,
    ) -> bool:
        cfg = self.cfg
        bar = exec_df.iloc[exec_idx]
        ts  = bar.name

        # Gate 1: session filter (London + NY)
        if not is_in_session(ts):
            return False

        # Gate 2: D1 trend
        d1_bias = get_trend_at_h1_bar(ts, d1_trend)
        if d1_bias == TrendLabel.NEUTRAL:
            return False

        # Gate 3: W1 must not contradict D1 (W1 neutral is allowed)
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

        # Gate 6: H4 OB ∩ H4 Fibonacci confluence zone
        confluence = find_confluence(active_h4_obs, fib_levels, direction, h4_atr_val)
        if confluence is None:
            return False

        # Gate 7: entry trigger on execution TF inside H4 zone
        signal = check_entry_trigger(exec_df, exec_idx, direction, confluence)
        if signal is None:
            return False

        # Gate 8: risk setup.
        # SL is anchored to the H4 Fibonacci zone boundary + 0.5×H4 ATR buffer.
        # Using H4 ATR (the signal TF) gives a SL appropriate for H4-scale analysis;
        # H1 ATR is ~4× too tight and leads to excessive noise-induced SL hits.
        if h1_idx < 0:
            return False
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
            atr=h4_atr_val,   # H4 ATR: SL wide enough to survive H1 noise
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
