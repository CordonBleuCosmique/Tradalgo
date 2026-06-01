from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
import numpy as np


@dataclass
class FairValueGap:
    bar_idx: int          # index of middle candle
    timestamp: pd.Timestamp
    direction: str        # "bullish" | "bearish"
    gap_low: float
    gap_high: float
    is_filled: bool = False


def detect_fvgs(df: pd.DataFrame) -> list[FairValueGap]:
    """
    Detect Fair Value Gaps (3-candle imbalance pattern).

    Bullish FVG: high[i-1] < low[i+1]  (gap between candle i-1 high and candle i+1 low)
    Bearish FVG: low[i-1] > high[i+1]

    Middle candle is at index i. Confirmed when bar i+1 closes.
    Engine must filter: fvg.bar_idx <= current_idx - 2
    """
    fvgs: list[FairValueGap] = []
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)

    for i in range(1, n - 1):
        if highs[i - 1] < lows[i + 1]:
            fvgs.append(FairValueGap(
                bar_idx=i,
                timestamp=df.index[i],
                direction="bullish",
                gap_low=highs[i - 1],
                gap_high=lows[i + 1],
            ))
        elif lows[i - 1] > highs[i + 1]:
            fvgs.append(FairValueGap(
                bar_idx=i,
                timestamp=df.index[i],
                direction="bearish",
                gap_low=highs[i + 1],
                gap_high=lows[i - 1],
            ))

    return fvgs


def update_fvg_fills(fvgs: list[FairValueGap], bar: pd.Series, direction: str) -> None:
    for fvg in fvgs:
        if fvg.is_filled or fvg.direction != direction:
            continue
        if direction == "bullish" and bar["Low"] <= fvg.gap_low:
            fvg.is_filled = True
        elif direction == "bearish" and bar["High"] >= fvg.gap_high:
            fvg.is_filled = True
