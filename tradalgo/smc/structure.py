from __future__ import annotations
from enum import Enum
import pandas as pd
import numpy as np


class MarketStructure(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


def classify_structure(swing_df: pd.DataFrame, lookback: int = 2) -> pd.Series:
    """
    Classify D1 market structure bar-by-bar using recent swing sequence.
    lookback=2 means we need 2 consecutive HH+HL (or LH+LL) to confirm trend.
    """
    n = len(swing_df)
    structure = [MarketStructure.NEUTRAL] * n

    # Collect swings incrementally (O(n) passes overall)
    sh_times: list[int] = []
    sl_times: list[int] = []
    sh_vals: list[float] = []
    sl_vals: list[float] = []

    for i in range(n):
        v_sh = swing_df["swing_high"].iloc[i]
        v_sl = swing_df["swing_low"].iloc[i]
        if not np.isnan(v_sh):
            sh_times.append(i)
            sh_vals.append(v_sh)
        if not np.isnan(v_sl):
            sl_times.append(i)
            sl_vals.append(v_sl)

        if len(sh_vals) < lookback + 1 or len(sl_vals) < lookback + 1:
            continue

        last_sh = sh_vals[-(lookback + 1):]
        last_sl = sl_vals[-(lookback + 1):]

        sh_hh = all(last_sh[j] > last_sh[j - 1] for j in range(1, len(last_sh)))
        sl_hl = all(last_sl[j] > last_sl[j - 1] for j in range(1, len(last_sl)))
        sh_lh = all(last_sh[j] < last_sh[j - 1] for j in range(1, len(last_sh)))
        sl_ll = all(last_sl[j] < last_sl[j - 1] for j in range(1, len(last_sl)))

        if sh_hh and sl_hl:
            structure[i] = MarketStructure.BULLISH
        elif sh_lh and sl_ll:
            structure[i] = MarketStructure.BEARISH

    return pd.Series(structure, index=swing_df.index)
