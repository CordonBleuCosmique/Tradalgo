from __future__ import annotations
import pandas as pd


def get_liquidity_levels(
    swing_df: pd.DataFrame,
    current_price: float,
    direction: str,
    n_levels: int = 5,
) -> list[float]:
    """
    Return nearest unmitigated liquidity levels from confirmed swings.

    direction="bullish" → swing highs above current price (TP targets for longs)
    direction="bearish" → swing lows below current price (TP targets for shorts)
    """
    if direction == "bullish":
        levels = swing_df["swing_high"].dropna()
        levels = levels[levels > current_price].sort_values(ascending=True)
    else:
        levels = swing_df["swing_low"].dropna()
        levels = levels[levels < current_price].sort_values(ascending=False)

    return levels.values[:max(n_levels, 20)].tolist()
