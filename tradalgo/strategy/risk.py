from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from tradalgo.smc.order_blocks import OrderBlock

PIP = 0.0001            # 1 pip for EURUSD
PIP_VALUE_USD = 10.0    # USD per pip per standard lot (100,000 units)
MIN_LOT = 0.01


@dataclass
class TradeSetup:
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_usd: float
    rr_ratio: float


def calculate_trade_setup(
    direction: str,
    entry_price: float,
    ob: OrderBlock,
    atr: float,
    liquidity_levels: list[float],
    account_equity: float,
    zone_low: float,
    zone_high: float,
    min_rr: float = 2.0,
    risk_pct: float = 0.01,
    spread_pips: float = 1.5,
) -> Optional[TradeSetup]:
    """
    Compute SL, TP, and lot size. Returns None if no TP satisfies min RR.

    Spread is accounted for at entry (added for longs, subtracted for shorts).
    SL is anchored to the golden zone boundary (the invalidation level), with
    a 0.5×ATR buffer. The OB provides structural confluence context; the zone
    itself defines trade invalidation.
    TP must be at a liquidity level with RR >= min_rr.
    """
    spread = spread_pips * PIP

    if direction == "bullish":
        effective_entry = entry_price + spread
        # SL: below the 61.8% level (golden zone low) — break below invalidates setup
        sl = zone_low - 0.5 * atr
        risk = effective_entry - sl
    else:
        effective_entry = entry_price - spread
        # SL: above the 50% level (golden zone high) — break above invalidates setup
        sl = zone_high + 0.5 * atr
        risk = sl - effective_entry

    if risk <= PIP:
        return None

    # Find first liquidity level that satisfies min RR
    tp = None
    actual_rr = 0.0
    for level in liquidity_levels:
        if direction == "bullish":
            reward = level - effective_entry
        else:
            reward = effective_entry - level
        if reward <= 0:
            continue
        rr = reward / risk
        if rr >= min_rr:
            tp = level
            actual_rr = rr
            break

    if tp is None:
        return None

    # Position sizing: risk_pct of equity
    risk_usd = account_equity * risk_pct
    sl_pips = risk / PIP
    raw_lot = risk_usd / (sl_pips * PIP_VALUE_USD)
    lot_size = max(MIN_LOT, round(raw_lot, 2))

    return TradeSetup(
        entry_price=effective_entry,
        stop_loss=sl,
        take_profit=tp,
        lot_size=lot_size,
        risk_usd=risk_usd,
        rr_ratio=round(actual_rr, 2),
    )
