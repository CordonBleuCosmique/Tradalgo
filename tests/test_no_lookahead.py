"""
Anti-look-ahead bias tests.

For each module, we verify that the value computed at bar i is identical
whether we run on the full dataset or on data truncated at bar i.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from tradalgo.indicators.ema import ema
from tradalgo.indicators.atr import atr as calc_atr
from tradalgo.indicators.swing import find_swings
from tradalgo.smc.order_blocks import detect_order_blocks
from tradalgo.smc.fvg import detect_fvgs


# ── Fixtures ─────────────────────────────────────────────────────────────

def make_ohlc(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 1.08 + np.cumsum(rng.normal(0, 0.0005, n))
    opens  = closes + rng.normal(0, 0.0002, n)
    highs  = np.maximum(opens, closes) + rng.uniform(0.0001, 0.001, n)
    lows   = np.minimum(opens, closes) - rng.uniform(0.0001, 0.001, n)
    idx = pd.date_range("2023-01-02", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes}, index=idx
    )


# ── EMA ──────────────────────────────────────────────────────────────────

def test_ema_no_lookahead():
    df = make_ohlc(200)
    check = 80
    full_val = ema(df["Close"], 20).iloc[check]
    trunc_val = ema(df["Close"].iloc[: check + 1], 20).iloc[-1]
    assert abs(full_val - trunc_val) < 1e-10, "EMA look-ahead bias detected"


# ── ATR ──────────────────────────────────────────────────────────────────

def test_atr_no_lookahead():
    df = make_ohlc(200)
    check = 80
    full_val = calc_atr(df, 14).iloc[check]
    trunc_val = calc_atr(df.iloc[: check + 1], 14).iloc[-1]
    assert abs(full_val - trunc_val) < 1e-10, "ATR look-ahead bias detected"


# ── Swing detection ───────────────────────────────────────────────────────

def test_swing_no_lookahead():
    df = make_ohlc(300)
    left = right = 5
    check = 120
    # Safe index: swing at (check - right) is confirmed when bar `check` prints
    safe = check - right

    full_sh  = find_swings(df, left, right)["swing_high"].iloc[safe]
    trunc_sh = find_swings(df.iloc[: check + 1], left, right)["swing_high"].iloc[safe]

    both_nan = np.isnan(full_sh) and np.isnan(trunc_sh)
    assert both_nan or abs(full_sh - trunc_sh) < 1e-10, \
        "Swing detection look-ahead bias detected"


# ── Order Blocks ─────────────────────────────────────────────────────────

def test_ob_confirmation_invariant():
    df   = make_ohlc(300)
    atr_ = calc_atr(df, 14)
    obs  = detect_order_blocks(df, atr_)

    for ob in obs:
        # Confirmation index must be within the dataset
        assert ob.confirmation_bar_idx < len(df)
        # The OB candle must precede its confirmation
        assert ob.bar_idx < ob.confirmation_bar_idx

        # Re-run on truncated data (up to confirmation bar)
        trunc_obs = detect_order_blocks(
            df.iloc[: ob.confirmation_bar_idx + 1],
            atr_.iloc[: ob.confirmation_bar_idx + 1],
        )
        # This OB must exist identically in the truncated run
        matching = [o for o in trunc_obs if o.bar_idx == ob.bar_idx and o.direction == ob.direction]
        assert matching, f"OB at bar {ob.bar_idx} vanishes when future data removed — look-ahead bias"
        assert abs(matching[0].zone_low  - ob.zone_low)  < 1e-10
        assert abs(matching[0].zone_high - ob.zone_high) < 1e-10


# ── FVG ──────────────────────────────────────────────────────────────────

def test_fvg_no_lookahead():
    df   = make_ohlc(300)
    fvgs = detect_fvgs(df)

    for fvg in fvgs:
        # Middle candle must not be at the last two bars
        assert fvg.bar_idx >= 1
        assert fvg.bar_idx < len(df) - 1

        # Re-run on data truncated at middle + 1 (the confirmation bar)
        trunc = detect_fvgs(df.iloc[: fvg.bar_idx + 2])
        matching = [f for f in trunc if f.bar_idx == fvg.bar_idx and f.direction == fvg.direction]
        assert matching, f"FVG at bar {fvg.bar_idx} vanishes when future data removed — look-ahead bias"
