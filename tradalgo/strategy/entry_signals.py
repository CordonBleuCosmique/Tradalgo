from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from tradalgo.strategy.confluence import ConfluenceZone


@dataclass
class EntrySignal:
    direction: str
    entry_price: float       # close of trigger candle
    confluence: ConfluenceZone
    trigger_type: str        # "engulfing" | "pin_bar"


def is_in_session(timestamp: pd.Timestamp) -> bool:
    """
    True if within active Forex trading sessions (UTC).
    Pre-London + London: 06:00–12:00, NY overlap + NY: 13:00–20:00
    """
    h = timestamp.hour
    return (6 <= h < 12) or (13 <= h < 20)


def _is_bullish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    return (
        prev["Close"] < prev["Open"]           # previous candle bearish
        and curr["Close"] > curr["Open"]        # current candle bullish
        and curr["Open"] <= prev["Close"]       # opens at or below prev close
        and curr["Close"] >= prev["Open"]       # closes at or above prev open
    )


def _is_bearish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    return (
        prev["Close"] > prev["Open"]
        and curr["Close"] < curr["Open"]
        and curr["Open"] >= prev["Close"]
        and curr["Close"] <= prev["Open"]
    )


def _is_hammer(bar: pd.Series) -> bool:
    body = abs(bar["Close"] - bar["Open"])
    full_range = bar["High"] - bar["Low"]
    if full_range == 0 or body == 0:
        return False
    lower_wick = min(bar["Open"], bar["Close"]) - bar["Low"]
    upper_wick = bar["High"] - max(bar["Open"], bar["Close"])
    return (
        lower_wick >= 2 * body
        and upper_wick <= 0.3 * full_range
        and bar["Close"] > bar["Open"]
    )


def _is_momentum_close_bull(bar: pd.Series) -> bool:
    """Candle closes in the upper 30% of its range — rejection of lows, bullish momentum."""
    full_range = bar["High"] - bar["Low"]
    if full_range < 1e-6:
        return False
    close_position = (bar["Close"] - bar["Low"]) / full_range
    return close_position >= 0.70 and bar["Close"] > bar["Open"]


def _is_momentum_close_bear(bar: pd.Series) -> bool:
    """Candle closes in the lower 30% of its range — rejection of highs, bearish momentum."""
    full_range = bar["High"] - bar["Low"]
    if full_range < 1e-6:
        return False
    close_position = (bar["Close"] - bar["Low"]) / full_range
    return close_position <= 0.30 and bar["Close"] < bar["Open"]


def _is_shooting_star(bar: pd.Series) -> bool:
    body = abs(bar["Close"] - bar["Open"])
    full_range = bar["High"] - bar["Low"]
    if full_range == 0 or body == 0:
        return False
    upper_wick = bar["High"] - max(bar["Open"], bar["Close"])
    lower_wick = min(bar["Open"], bar["Close"]) - bar["Low"]
    return (
        upper_wick >= 2 * body
        and lower_wick <= 0.3 * full_range
        and bar["Close"] < bar["Open"]
    )


def check_entry_trigger(
    df: pd.DataFrame,
    current_idx: int,
    direction: str,
    confluence: ConfluenceZone,
) -> Optional[EntrySignal]:
    """
    Check if the current bar produces a valid entry trigger inside the confluence zone.
    Entry price is the CLOSE of the trigger candle (no look-ahead).
    """
    if current_idx < 1:
        return None

    curr = df.iloc[current_idx]
    prev = df.iloc[current_idx - 1]

    # At least the low (for bull) or high (for bear) must touch the zone
    if direction == "bullish":
        touching = curr["Low"] <= confluence.high and curr["Close"] >= confluence.low
    else:
        touching = curr["High"] >= confluence.low and curr["Close"] <= confluence.high

    if not touching:
        return None

    if direction == "bullish":
        if _is_bullish_engulfing(prev, curr):
            return EntrySignal(direction, curr["Close"], confluence, "engulfing")
        if _is_hammer(curr):
            return EntrySignal(direction, curr["Close"], confluence, "pin_bar")
        if _is_momentum_close_bull(curr):
            return EntrySignal(direction, curr["Close"], confluence, "momentum_close")
    else:
        if _is_bearish_engulfing(prev, curr):
            return EntrySignal(direction, curr["Close"], confluence, "engulfing")
        if _is_shooting_star(curr):
            return EntrySignal(direction, curr["Close"], confluence, "pin_bar")
        if _is_momentum_close_bear(curr):
            return EntrySignal(direction, curr["Close"], confluence, "momentum_close")

    return None
