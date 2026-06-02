from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf


@dataclass
class MarketData:
    h1: pd.DataFrame                    # OHLCV, UTC-indexed
    d1: pd.DataFrame                    # OHLCV, UTC-indexed
    m15: pd.DataFrame = None            # M15 OHLCV — populated when source is M1-level CSV


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).capitalize() for c in df.columns]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


def _load_yfinance(ticker: str, interval: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(ticker, start=start, end=end, interval=interval,
                     auto_adjust=True, progress=False)
    return _normalize_df(df)


def _load_csv(csv_path: str, source: str) -> pd.DataFrame:
    path = Path(csv_path)
    if source == "histdata_csv":
        # Auto-detect Histdata.com format variant:
        #   Modern (6 cols):  YYYYMMDD HHMMSS;Open;High;Low;Close;Volume
        #   Legacy (7 cols):  YYYY.MM.DD;HH:MM;Open;High;Low;Close;Volume
        sep = ";" if ";" in path.read_text(encoding="utf-8", errors="ignore")[:200] else ","
        raw = pd.read_csv(path, sep=sep, header=None)
        if raw.shape[1] == 6:
            # Modern single-field datetime
            raw.columns = ["datetime", "Open", "High", "Low", "Close", "Volume"]
            raw["datetime"] = pd.to_datetime(raw["datetime"], format="%Y%m%d %H%M%S")
        else:
            # Legacy two-field datetime (date + time)
            raw.columns = ["date", "time", "Open", "High", "Low", "Close", "Volume"]
            raw["datetime"] = pd.to_datetime(
                raw["date"].astype(str) + " " + raw["time"].astype(str),
                infer_datetime_format=True,
            )
        df = raw.set_index("datetime")[["Open", "High", "Low", "Close", "Volume"]]
        df.index = df.index.tz_localize("UTC")
    elif source == "mt4_csv":
        # MT4 format: Date,Time,Open,High,Low,Close,Volume
        df = pd.read_csv(path)
        df.columns = [c.strip() for c in df.columns]
        if "Date" in df.columns and "Time" in df.columns:
            df["datetime"] = pd.to_datetime(df["Date"].astype(str) + " " + df["Time"].astype(str))
            df = df.set_index("datetime")[["Open", "High", "Low", "Close", "Volume"]]
        else:
            df = df.set_index(df.columns[0])
            df.index = pd.to_datetime(df.index)
        df.index = df.index.tz_localize("UTC")
    else:
        # Generic: first column is datetime, then OHLCV
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df = _normalize_df(df)
    return df.sort_index()


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["Open"])


def _resample_to_h1(df: pd.DataFrame) -> pd.DataFrame:
    return _resample(df, "1h")


def _cache_path(cache_dir: str, key: str) -> Path:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return Path(cache_dir) / f"{key}.pkl"


def _save_cache(df: pd.DataFrame, path: Path) -> None:
    df.to_pickle(path)


def _load_cache(path: Path) -> pd.DataFrame:
    return pd.read_pickle(path)


def load_data(
    source: str = "yfinance",
    csv_path: str | None = None,
    ticker: str = "EURUSD=X",
    start: str = "2020-01-01",
    end: str = "2025-01-01",
    cache_dir: str = "data_cache",
    force_download: bool = False,
) -> MarketData:
    """
    Load EURUSD H1 and D1 data.

    source: "yfinance" | "histdata_csv" | "mt4_csv" | "generic_csv"
    csv_path: required when source != "yfinance"
    start/end: backtest date range; warm-up year added internally
    """
    warmup_start = str(pd.Timestamp(start) - pd.DateOffset(years=2))[:10]

    safe_end = end.replace("-", "")
    h1_cache = _cache_path(cache_dir, f"H1_{source}_{start.replace('-','')}_{safe_end}")
    d1_cache = _cache_path(cache_dir, f"D1_{start.replace('-','')}_{safe_end}")

    m15 = None

    # --- H1 ---
    if not force_download and h1_cache.exists():
        h1 = _load_cache(h1_cache)
    else:
        if source == "yfinance":
            h1 = _load_yfinance(ticker, "1h", warmup_start, end)
        else:
            assert csv_path, "csv_path is required for non-yfinance sources"
            raw = _load_csv(csv_path, source)
            bars_per_day = len(raw) / max(1, (raw.index[-1] - raw.index[0]).days)
            if bars_per_day > 200:
                # M1-level data: produce M15 and H1 from same raw feed
                m15 = _resample(raw, "15min").loc[warmup_start:end]
                h1  = _resample(raw, "1h").loc[warmup_start:end]
            elif bars_per_day > 50:
                h1  = _resample_to_h1(raw).loc[warmup_start:end]
            else:
                h1  = raw.loc[warmup_start:end]
        _save_cache(h1, h1_cache)

    # Cache M15 separately (only when derived from M1 source)
    if m15 is not None:
        m15_cache = _cache_path(cache_dir, f"M15_{source}_{start.replace('-','')}_{safe_end}")
        if not force_download and m15_cache.exists():
            m15 = _load_cache(m15_cache)
        else:
            _save_cache(m15, m15_cache)

    # --- D1 ---
    if not force_download and d1_cache.exists():
        d1 = _load_cache(d1_cache)
    else:
        if source == "yfinance":
            d1 = _load_yfinance(ticker, "1d", warmup_start, end)
        else:
            d1 = _resample(h1, "1D")
            d1 = d1[d1.index.dayofweek < 5]
        _save_cache(d1, d1_cache)

    return MarketData(h1=h1, d1=d1, m15=m15)
