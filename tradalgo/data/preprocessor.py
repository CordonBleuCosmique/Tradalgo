from __future__ import annotations
import pandas as pd


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Drop phantom bars (all OHLC identical — market closure artifacts)
    mask = (df["Open"] == df["High"]) & (df["High"] == df["Low"]) & (df["Low"] == df["Close"])
    df = df[~mask]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    # Drop weekends
    df = df[df.index.dayofweek < 5]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def align_to_trading_hours(df: pd.DataFrame) -> pd.DataFrame:
    # Drop Sunday bars before 21:00 UTC (market not open)
    # Drop Friday bars at or after 21:00 UTC
    is_sunday_early = (df.index.dayofweek == 6) & (df.index.hour < 21)
    is_friday_late = (df.index.dayofweek == 4) & (df.index.hour >= 21)
    return df[~is_sunday_early & ~is_friday_late]


def preprocess(h1: pd.DataFrame, d1: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    h1 = clean(h1)
    h1 = align_to_trading_hours(h1)
    d1 = clean(d1)
    return h1, d1
