from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from tradalgo.smc.order_blocks import OrderBlock
from tradalgo.strategy.fibonacci import FibLevels


@dataclass
class ConfluenceZone:
    low: float
    high: float
    ob: OrderBlock
    fib_levels: FibLevels


def _ranges_overlap(
    low1: float, high1: float,
    low2: float, high2: float,
    tolerance: float = 0.0,
) -> bool:
    return (low1 - tolerance) <= high2 and (low2 - tolerance) <= high1


def find_confluence(
    active_obs: list[OrderBlock],
    fib_levels: FibLevels,
    direction: str,
    atr: float,
    swing_range_tolerance: float = 0.30,
) -> Optional[ConfluenceZone]:
    """
    Find the best OB + Fibonacci confluence zone.

    ICT/SMC logic:
    - Fibonacci golden zone (50–61.8%) is the entry target.
    - An active OB in the correct structural position provides the SL anchor.

    For BULLISH: OB must be below or overlapping the golden zone (structural support).
    For BEARISH: OB must be above or overlapping the golden zone (structural resistance).

    No maximum distance constraint — the OB can be far below/above as long as it is
    on the correct side. The closest OB is preferred. The entry zone is always the
    Fibonacci golden zone; the OB's outer edge is used for stop placement.
    """
    fib_low  = fib_levels.level_618   # deeper retracement
    fib_high = fib_levels.level_500   # shallower retracement

    if fib_low > fib_high:
        fib_low, fib_high = fib_high, fib_low

    best_ob   = None
    best_dist = float("inf")

    for ob in reversed(active_obs):
        if ob.direction != direction or ob.is_mitigated:
            continue

        if _ranges_overlap(ob.zone_low, ob.zone_high, fib_low, fib_high):
            dist = 0.0
        elif direction == "bullish":
            if ob.zone_high > fib_low:
                # OB is above the golden zone low — wrong side, skip
                continue
            dist = fib_low - ob.zone_high   # positive: OB below zone
        else:
            if ob.zone_low < fib_high:
                # OB is below the golden zone high — wrong side, skip
                continue
            dist = ob.zone_low - fib_high   # positive: OB above zone

        if dist < best_dist:
            best_dist = dist
            best_ob   = ob

    if best_ob is None:
        return None

    return ConfluenceZone(
        low=fib_low,
        high=fib_high,
        ob=best_ob,
        fib_levels=fib_levels,
    )
