from __future__ import annotations
import pandas as pd
import numpy as np


def find_swings(
    df: pd.DataFrame,
    left_bars: int = 5,
    right_bars: int = 5,
) -> pd.DataFrame:
    """
    Detect pivot swing highs and lows.

    A swing high at bar i is confirmed only after bar i+right_bars has printed.
    The engine must access results as: swing_df.iloc[:idx - right_bars]
    to avoid look-ahead bias.
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)

    swing_high = np.full(n, np.nan)
    swing_low = np.full(n, np.nan)

    for i in range(left_bars, n - right_bars):
        window_h = highs[i - left_bars: i + right_bars + 1]
        # Strict maximum (no ties)
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            swing_high[i] = highs[i]

        window_l = lows[i - left_bars: i + right_bars + 1]
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            swing_low[i] = lows[i]

    return pd.DataFrame(
        {"swing_high": swing_high, "swing_low": swing_low},
        index=df.index,
    )
