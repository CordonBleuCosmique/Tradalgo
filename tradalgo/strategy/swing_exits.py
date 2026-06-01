from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd

from tradalgo.strategy.confluence import ConfluenceZone


@dataclass
class SwingSignal:
    direction: str
    entry_price: float
    confluence: ConfluenceZone
    trigger_type: str


def check_swing_entry(
    df: pd.DataFrame,
    idx: int,
    direction: str,
    confluence: ConfluenceZone,
    atr_val: float,
    tap_tolerance_atr: float = 0.25,
) -> Optional[SwingSignal]:
    """
    Swing pullback trigger (D1).

    Looser than the intraday candlestick trigger: a swing entry fires when
    price taps the golden zone (within tap_tolerance_atr of it) and the bar
    CLOSES back inside or beyond the zone in the trend direction — i.e. the
    zone held and was not closed through. No engulfing/pin requirement,
    because D1 produces far fewer bars and demanding a precise pattern on
    the exact tap bar rejects most valid pullbacks.

    Entry price is the CLOSE of the trigger bar (no look-ahead).
    """
    if idx < 1:
        return None
    bar = df.iloc[idx]
    tol = tap_tolerance_atr * atr_val if atr_val > 0 else 0.0

    if direction == "bullish":
        tapped     = bar["Low"] <= confluence.high + tol
        held       = bar["Close"] >= confluence.low          # not closed through
        not_broken = bar["Close"] > bar["Low"]               # some rejection of lows
        if tapped and held and not_broken:
            return SwingSignal(direction, float(bar["Close"]), confluence, "zone_tap")
    else:
        tapped     = bar["High"] >= confluence.low - tol
        held       = bar["Close"] <= confluence.high
        not_broken = bar["Close"] < bar["High"]
        if tapped and held and not_broken:
            return SwingSignal(direction, float(bar["Close"]), confluence, "zone_tap")

    return None


def latest_confirmed_swing(
    swing_df: pd.DataFrame,
    idx: int,
    swing_right: int,
    kind: str,
) -> Optional[float]:
    """
    Return the most recent CONFIRMED swing price at or before bar `idx`.

    A pivot at position p is only confirmed once `swing_right` bars have
    printed after it, so we may only look at rows up to idx - swing_right.
    kind: "swing_low" | "swing_high".
    """
    safe_end = idx - swing_right
    if safe_end <= 0:
        return None
    col = swing_df[kind].iloc[:safe_end].dropna()
    if col.empty:
        return None
    return float(col.iloc[-1])


def update_trailing_stop(
    direction: str,
    current_sl: float,
    swing_df: pd.DataFrame,
    atr_val: float,
    idx: int,
    swing_right: int,
    trail_buffer_atr: float,
) -> float:
    """
    Structure-based trailing stop.

    Long : trail SL up to (latest confirmed swing low − buffer), never down.
    Short: trail SL down to (latest confirmed swing high + buffer), never up.

    Uses only confirmed swings → no look-ahead. Returns the (possibly
    unchanged) stop-loss price.
    """
    if np.isnan(atr_val) or atr_val <= 0:
        return current_sl
    buffer = trail_buffer_atr * atr_val

    if direction == "bullish":
        sl_anchor = latest_confirmed_swing(swing_df, idx, swing_right, "swing_low")
        if sl_anchor is None:
            return current_sl
        candidate = sl_anchor - buffer
        return max(current_sl, candidate)   # ratchet up only
    else:
        sl_anchor = latest_confirmed_swing(swing_df, idx, swing_right, "swing_high")
        if sl_anchor is None:
            return current_sl
        candidate = sl_anchor + buffer
        return min(current_sl, candidate)   # ratchet down only
