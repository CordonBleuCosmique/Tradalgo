from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import numpy as np


@dataclass
class FibLevels:
    swing_high: float
    swing_low: float
    level_236: float
    level_382: float
    level_500: float
    level_618: float
    level_786: float

    @property
    def golden_zone_low(self) -> float:
        return self.level_500

    @property
    def golden_zone_high(self) -> float:
        return self.level_618


def compute_fib_retracement(swing_high: float, swing_low: float) -> FibLevels:
    diff = swing_high - swing_low
    return FibLevels(
        swing_high=swing_high,
        swing_low=swing_low,
        level_236=swing_high - 0.236 * diff,
        level_382=swing_high - 0.382 * diff,
        level_500=swing_high - 0.500 * diff,
        level_618=swing_high - 0.618 * diff,
        level_786=swing_high - 0.786 * diff,
    )


def find_anchor_swing(
    swing_df: pd.DataFrame,
    atr_series: pd.Series,
    direction: str,
    lookback_bars: int = 50,
    min_range_atr: float = 1.5,
) -> tuple[float, float] | None:
    """
    Find the most recent significant swing pair for Fibonacci retracement.

    Only uses swing_df rows already confirmed (caller slices before passing).
    Returns (swing_high, swing_low) or None.
    """
    if len(swing_df) < 2:
        return None

    recent = swing_df.iloc[-lookback_bars:]
    atr_val = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0.0
    if np.isnan(atr_val):
        return None

    min_range = min_range_atr * atr_val if atr_val > 0 else 0.0

    if direction == "bullish":
        # BULLISH: find the swing_high/swing_low pair with the LARGEST range.
        # Anchor = significant swing_high + the swing_low that came BEFORE it.
        # Fibonacci is drawn from swing_low → swing_high.
        # Golden zone (50–61.8%) = expected pullback support area.
        swing_highs = recent["swing_high"].dropna()
        swing_lows  = recent["swing_low"].dropna()
        if swing_highs.empty or swing_lows.empty:
            return None

        best: tuple[float, float] | None = None
        best_range = 0.0

        for sh_time, sh_price in swing_highs.items():
            sl_candidates = swing_lows[swing_lows.index < sh_time]
            if sl_candidates.empty:
                continue
            sl_price = float(sl_candidates.iloc[-1])   # last low before this high
            rng = sh_price - sl_price
            if sh_price <= sl_price or rng < min_range:
                continue
            if rng > best_range:
                best_range = rng
                best = (float(sh_price), sl_price)

        return best

    else:  # bearish
        # BEARISH: swing_low + the swing_high that came BEFORE it.
        # Golden zone = expected retracement resistance area.
        swing_lows  = recent["swing_low"].dropna()
        swing_highs = recent["swing_high"].dropna()
        if swing_lows.empty or swing_highs.empty:
            return None

        best = None
        best_range = 0.0

        for sl_time, sl_price in swing_lows.items():
            sh_candidates = swing_highs[swing_highs.index < sl_time]
            if sh_candidates.empty:
                continue
            sh_price = float(sh_candidates.iloc[-1])
            rng = sh_price - sl_price
            if sl_price >= sh_price or rng < min_range:
                continue
            if rng > best_range:
                best_range = rng
                best = (float(sh_price), float(sl_price))

        return best
