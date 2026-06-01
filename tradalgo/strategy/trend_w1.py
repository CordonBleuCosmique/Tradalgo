from __future__ import annotations
import pandas as pd

from tradalgo.indicators.ema import ema
from tradalgo.indicators.swing import find_swings
from tradalgo.smc.structure import classify_structure, MarketStructure
from tradalgo.strategy.trend_filter import TrendLabel


def resample_weekly(d1_df: pd.DataFrame) -> pd.DataFrame:
    """Resample D1 OHLCV → W1 (weekly bars, week anchored on Monday)."""
    w1 = d1_df.resample("W-MON", label="left", closed="left").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(subset=["Open"])
    return w1


def compute_weekly_trend(
    w1_df: pd.DataFrame,
    ema_fast: int = 20,
    ema_slow: int = 50,
) -> pd.Series:
    """
    Macro trend label for every W1 bar.

    Weekly EMAs are shorter than the D1 50/200 pair because 200 weeks of
    data (~4 years) is impractical; EMA20/50 on weekly ≈ EMA100/250 on daily.

    Consumed via get_weekly_trend_at (backward lookup) — only weekly bars
    that closed strictly before the current D1 bar are ever read, so it is
    safe to compute over the full dataset.
    """
    df = w1_df.copy()
    df["ema_fast"] = ema(df["Close"], ema_fast)
    df["ema_slow"] = ema(df["Close"], ema_slow)

    swings    = find_swings(df, left_bars=2, right_bars=2)
    structure = classify_structure(swings, lookback=1)

    labels: list[TrendLabel] = []
    for i in range(len(df)):
        row   = df.iloc[i]
        ms    = structure.iloc[i]
        ef    = row["ema_fast"]
        es    = row["ema_slow"]
        close = row["Close"]

        if not (pd.notna(ef) and pd.notna(es)):
            labels.append(TrendLabel.NEUTRAL)
            continue

        above_slow  = close > es
        golden      = ef > es
        dead        = ef < es
        bullish_ema = above_slow and golden
        bearish_ema = (not above_slow) and dead
        bull_struct = ms == MarketStructure.BULLISH and close > es
        bear_struct = ms == MarketStructure.BEARISH and close < es

        if bullish_ema or bull_struct:
            labels.append(TrendLabel.BULLISH)
        elif bearish_ema or bear_struct:
            labels.append(TrendLabel.BEARISH)
        else:
            labels.append(TrendLabel.NEUTRAL)

    return pd.Series(labels, index=df.index, name="w1_trend")


def get_weekly_trend_at(ts: pd.Timestamp, w1_trend: pd.Series) -> TrendLabel:
    """Return the last weekly trend label whose bar closed BEFORE ts."""
    ts = pd.Timestamp(ts)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    # A weekly bar labelled at its left edge only "closes" ~7 days later;
    # require a full week to have elapsed to avoid look-ahead.
    mask = (w1_trend.index + pd.Timedelta(days=7)) <= ts
    if not mask.any():
        return TrendLabel.NEUTRAL
    return w1_trend[mask].iloc[-1]
