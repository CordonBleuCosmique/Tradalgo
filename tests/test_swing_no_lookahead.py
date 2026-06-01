"""
Anti-look-ahead tests for the swing (D1 macro) strategy components.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from tradalgo.indicators.atr import atr as calc_atr
from tradalgo.indicators.swing import find_swings
from tradalgo.strategy.trend_w1 import (
    resample_weekly, compute_weekly_trend, get_weekly_trend_at,
)
from tradalgo.strategy.swing_exits import latest_confirmed_swing, update_trailing_stop


def make_daily(n: int = 600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 1.10 + np.cumsum(rng.normal(0, 0.004, n))
    opens  = closes + rng.normal(0, 0.002, n)
    highs  = np.maximum(opens, closes) + rng.uniform(0.001, 0.006, n)
    lows   = np.minimum(opens, closes) - rng.uniform(0.001, 0.006, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes,
         "Volume": rng.integers(1000, 5000, n)}, index=idx
    )


def test_weekly_trend_backward_lookup_no_lookahead():
    """get_weekly_trend_at must never read a weekly bar that hasn't fully closed."""
    d1 = make_daily(600)
    w1 = resample_weekly(d1)
    trend = compute_weekly_trend(w1, 20, 50)

    # Pick a mid D1 timestamp and confirm the label comes from a week that
    # closed at least 7 days earlier.
    probe_ts = d1.index[300]
    label = get_weekly_trend_at(probe_ts, trend)

    mask = (trend.index + pd.Timedelta(days=7)) <= probe_ts
    if mask.any():
        expected_idx = trend[mask].index[-1]
        assert expected_idx + pd.Timedelta(days=7) <= probe_ts
        assert label == trend[mask].iloc[-1]


def test_latest_confirmed_swing_excludes_future():
    """A swing pivot must only be visible swing_right bars after it printed."""
    d1 = make_daily(400)
    right = 3
    swings = find_swings(d1, 3, right)
    idx = 200

    val = latest_confirmed_swing(swings, idx, right, "swing_low")
    if val is not None:
        # The returned swing must come from before idx - right
        col = swings["swing_low"].iloc[: idx - right].dropna()
        assert abs(col.iloc[-1] - val) < 1e-12


def test_trailing_stop_ratchets_one_direction():
    """Trailing stop must only tighten, never loosen."""
    d1 = make_daily(400)
    swings = find_swings(d1, 3, 3)
    atr = calc_atr(d1, 14)

    # Bullish: SL only moves up
    sl = 1.0500
    for idx in range(120, 200):
        av = float(atr.iloc[idx])
        new_sl = update_trailing_stop("bullish", sl, swings, av, idx, 3, 1.0)
        assert new_sl >= sl - 1e-12, "Bullish trailing stop moved down"
        sl = new_sl

    # Bearish: SL only moves down
    sl = 1.3000
    for idx in range(120, 200):
        av = float(atr.iloc[idx])
        new_sl = update_trailing_stop("bearish", sl, swings, av, idx, 3, 1.0)
        assert new_sl <= sl + 1e-12, "Bearish trailing stop moved up"
        sl = new_sl
