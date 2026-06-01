from __future__ import annotations
import numpy as np
import pandas as pd


def get_liquidity_levels(
    swing_df: pd.DataFrame,
    current_price: float,
    direction: str,
    atr: float = 0.0,
    n_levels: int = 10,
) -> list[float]:
    """
    Return significant liquidity levels from confirmed swings.

    For bullish  → swing highs above current price (ascending, closest first).
    For bearish → swing lows  below current price (descending, closest first).

    When atr > 0, deduplicates clusters: keeps only the first level within each
    1×ATR window, so the returned list spans meaningful price distances rather
    than returning 20 micro-swings within a 3-pip range.
    Returns at most n_levels levels.
    """
    if direction == "bullish":
        levels = swing_df["swing_high"].dropna()
        levels = levels[levels > current_price].sort_values(ascending=True)
    else:
        levels = swing_df["swing_low"].dropna()
        levels = levels[levels < current_price].sort_values(ascending=False)

    vals = levels.values
    if len(vals) == 0:
        return []

    if atr <= 0:
        return vals[:n_levels].tolist()

    # Deduplicate: skip levels that are within 0.5×ATR of the last kept level
    min_gap = 0.5 * atr
    result = [vals[0]]
    for v in vals[1:]:
        if abs(v - result[-1]) >= min_gap:
            result.append(v)
        if len(result) >= n_levels:
            break
    return result
