from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

PIP = 0.0001
PIP_VALUE_USD = 10.0     # USD per pip per standard lot (100k units)
UNITS_PER_LOT = 100_000  # base-currency units in 1 standard lot
MIN_LOT = 0.01


@dataclass
class SwingSetup:
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_usd: float
    rr_ratio: float


def calculate_swing_setup(
    direction: str,
    entry_price: float,
    atr: float,
    zone_low: float,
    zone_high: float,
    liquidity_levels: list[float],
    account_equity: float,
    tp_rr: float = 6.0,
    risk_pct: float = 0.03,
    spread_pips: float = 1.5,
    min_lot: float = MIN_LOT,
    sizing_mode: str = "risk",
    margin_pct: float = 0.10,
    leverage: float = 30.0,
) -> Optional[SwingSetup]:
    """
    Risk model for swing trades.

    SL is anchored to the golden-zone boundary with a 1.0×ATR buffer (wider
    than intraday because D1 noise is larger). TP is a generous backstop at
    tp_rr × risk — the real exit is normally the structure-based trailing
    stop, but a hard TP caps the trade in case price gaps to target.

    If a liquidity level sits beyond the tp_rr backstop in the trade's
    favour, the nearest such level is used instead (lets winners aim for a
    real structural target rather than an arbitrary multiple).

    Position sizing (sizing_mode):
    - "risk"   : lot sized so SL distance risks risk_pct of equity (classic).
    - "margin" : each position commits margin_pct of *current* equity as
                 margin at the given leverage. Notional = margin_pct × equity
                 × leverage; lot = notional / (UNITS_PER_LOT × price). The
                 dollar risk then floats with the SL distance and is reported
                 in risk_usd for R-multiple tracking.
    """
    spread = spread_pips * PIP

    if direction == "bullish":
        effective_entry = entry_price + spread
        sl = zone_low - 1.0 * atr
        risk = effective_entry - sl
    else:
        effective_entry = entry_price - spread
        sl = zone_high + 1.0 * atr
        risk = sl - effective_entry

    if risk <= PIP:
        return None

    # Backstop TP at tp_rr × risk
    if direction == "bullish":
        tp = effective_entry + tp_rr * risk
        # If a liquidity level lies beyond the backstop, ride to it
        beyond = [lv for lv in liquidity_levels if lv > tp]
        if beyond:
            tp = min(beyond)
    else:
        tp = effective_entry - tp_rr * risk
        beyond = [lv for lv in liquidity_levels if lv < tp]
        if beyond:
            tp = max(beyond)

    reward = abs(tp - effective_entry)
    actual_rr = reward / risk
    sl_pips = risk / PIP

    # ── Position sizing ───────────────────────────────────────────────────
    if sizing_mode == "margin":
        # 0.01 lot = 1,000 EUR base units; required margin = 1,000 / leverage
        # → lot = (margin_pct × equity × leverage) / UNITS_PER_LOT
        raw_lot = (margin_pct * account_equity * leverage) / UNITS_PER_LOT
    else:
        # Risk-based: size so the SL distance risks risk_pct of equity.
        target_risk_usd = account_equity * risk_pct
        raw_lot = target_risk_usd / (sl_pips * PIP_VALUE_USD)

    lot_size = max(min_lot, round(raw_lot, 2))

    # Actual dollar risk implied by the final (rounded, floored) lot size.
    risk_usd = sl_pips * PIP_VALUE_USD * lot_size

    return SwingSetup(
        entry_price=effective_entry,
        stop_loss=sl,
        take_profit=tp,
        lot_size=lot_size,
        risk_usd=risk_usd,
        rr_ratio=round(actual_rr, 2),
    )
