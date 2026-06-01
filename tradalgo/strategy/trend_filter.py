from __future__ import annotations
from enum import Enum
import pandas as pd
import numpy as np

from tradalgo.indicators.ema import ema
from tradalgo.indicators.swing import find_swings
from tradalgo.smc.structure import classify_structure, MarketStructure


class TrendLabel(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


def compute_d1_trend(d1_df: pd.DataFrame) -> pd.Series:
    """
    Compute trend label for every D1 bar.

    Safe to compute on the full D1 dataset because it is consumed via
    get_trend_at_h1_bar which uses a backward merge — only D1 bars whose
    timestamp strictly precedes the H1 bar are ever read.
    """
    df = d1_df.copy()
    df["ema50"] = ema(df["Close"], 50)
    df["ema200"] = ema(df["Close"], 200)

    swings = find_swings(df, left_bars=3, right_bars=3)
    structure = classify_structure(swings, lookback=2)

    labels = []
    for i in range(len(df)):
        row = df.iloc[i]
        ms = structure.iloc[i]
        e50 = row["ema50"]
        e200 = row["ema200"]
        close = row["Close"]

        if not (pd.notna(e50) and pd.notna(e200)):
            labels.append(TrendLabel.NEUTRAL)
            continue

        # Primary filter: EMA200 slope (price side) + EMA crossover
        # Market structure (HH/HL or LH/LL) is used as a tiebreaker,
        # not as a hard gate — pure EMA crossover is the macro trigger.
        above_200 = close > e200
        golden    = e50 > e200      # golden cross
        dead      = e50 < e200      # death cross

        bullish_ema = above_200 and golden
        bearish_ema = (not above_200) and dead

        # Optional structure confirmation (loosens requirement: either EMA
        # crossover OR clear HH/HL structure above EMA200 qualifies)
        bullish_struct = ms == MarketStructure.BULLISH and close > e200
        bearish_struct = ms == MarketStructure.BEARISH and close < e200

        if bullish_ema or bullish_struct:
            labels.append(TrendLabel.BULLISH)
        elif bearish_ema or bearish_struct:
            labels.append(TrendLabel.BEARISH)
        else:
            labels.append(TrendLabel.NEUTRAL)

    return pd.Series(labels, index=df.index, name="trend")


def get_trend_at_h1_bar(
    h1_timestamp: pd.Timestamp,
    d1_trend: pd.Series,
) -> TrendLabel:
    """
    Backward lookup: return the last D1 trend label whose bar closed BEFORE
    the given H1 bar's timestamp.
    """
    ts = pd.Timestamp(h1_timestamp)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")

    # Only D1 bars with timestamp strictly before the H1 bar
    mask = d1_trend.index < ts
    if not mask.any():
        return TrendLabel.NEUTRAL
    return d1_trend[mask].iloc[-1]
