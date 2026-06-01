from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd
import numpy as np


@dataclass
class OrderBlock:
    bar_idx: int
    timestamp: pd.Timestamp
    direction: str          # "bullish" | "bearish"
    zone_low: float
    zone_high: float
    confirmation_bar_idx: int   # last bar of the impulse (must be < current_idx for visibility)
    is_mitigated: bool = False


def detect_order_blocks(
    df: pd.DataFrame,
    atr_series: pd.Series,
    impulse_bars: int = 3,
    impulse_threshold: float = 1.5,
) -> list[OrderBlock]:
    """
    Scan H1 OHLC for Order Blocks.

    Bullish OB: last bearish candle before a strong bullish impulse.
    Bearish OB: last bullish candle before a strong bearish impulse.

    An OB at bar i is only visible at current_idx > confirmation_bar_idx = i + impulse_bars.
    """
    obs: list[OrderBlock] = []
    closes = df["Close"].values
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    atr_vals = atr_series.values
    n = len(df)

    for i in range(n - impulse_bars):
        atv = atr_vals[i]
        if np.isnan(atv) or atv == 0:
            continue

        conf_idx = i + impulse_bars

        # Bullish OB: bearish candle at i, strong bullish impulse after.
        # Impulse measured as High-Low displacement (close[i+1..conf] all bullish).
        if closes[i] < opens[i]:
            impulse_move = highs[conf_idx] - lows[i]   # full displacement of the move
            all_bullish = all(closes[j] > opens[j] for j in range(i + 1, conf_idx + 1))
            if all_bullish and impulse_move > impulse_threshold * atv:
                zone_size = highs[i] - lows[i]
                if 0 < zone_size <= 3 * atv:
                    obs.append(OrderBlock(
                        bar_idx=i,
                        timestamp=df.index[i],
                        direction="bullish",
                        zone_low=lows[i],
                        zone_high=highs[i],
                        confirmation_bar_idx=conf_idx,
                    ))

        # Bearish OB: bullish candle at i, strong bearish impulse after.
        elif closes[i] > opens[i]:
            impulse_move = highs[i] - lows[conf_idx]   # full displacement of the move
            all_bearish = all(closes[j] < opens[j] for j in range(i + 1, conf_idx + 1))
            if all_bearish and impulse_move > impulse_threshold * atv:
                zone_size = highs[i] - lows[i]
                if 0 < zone_size <= 3 * atv:
                    obs.append(OrderBlock(
                        bar_idx=i,
                        timestamp=df.index[i],
                        direction="bearish",
                        zone_low=lows[i],
                        zone_high=highs[i],
                        confirmation_bar_idx=conf_idx,
                    ))

    return obs


def update_mitigation(obs: list[OrderBlock], bar: pd.Series, direction: str) -> None:
    """
    Mark OBs as mitigated per ICT rules:
    - Bullish OB: mitigated when the bar CLOSES below zone_low (wick alone is not enough)
    - Bearish OB: mitigated when the bar CLOSES above zone_high
    """
    close = bar["Close"]
    for ob in obs:
        if ob.is_mitigated or ob.direction != direction:
            continue
        if direction == "bullish" and close < ob.zone_low:
            ob.is_mitigated = True
        elif direction == "bearish" and close > ob.zone_high:
            ob.is_mitigated = True
