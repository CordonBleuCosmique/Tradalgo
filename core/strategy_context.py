"""
core/strategy_context.py

Builds the structured multi-timeframe market context handed to the LLM
prompt (core/prompt_system.md). Enforces the D1 -> H4 -> M15 hierarchy at
the data-fetch/computation level: full ICT pattern semantics (liquidity
sweep / BOS / CHoCH / order block / FVG) are simplified here via basic
swing-point structure detection; the rest of the judgment is delegated to
the LLM, which receives this structured context as data.

Candle dicts are expected to have at least: {"timestamp", "open", "high",
"low", "close"}, ordered oldest -> newest, with the LAST element being the
still-forming (not yet closed) candle -- standard broker trendbar API
behavior. `_last_closed_candle` explicitly drops it.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal, Optional


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: Literal["high", "low"]


def find_swing_points(candles: list[dict], lookback: int = 2) -> list[SwingPoint]:
    """
    Basic fractal swing detection: a candle at index i is a swing high if
    its high is the strict maximum among the `lookback` candles on each
    side (and analogous for swing lows). Only interior candles (with a
    full window on both sides) are considered.
    """
    swings: list[SwingPoint] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        window = candles[i - lookback : i + lookback + 1]
        high_i = candles[i]["high"]
        low_i = candles[i]["low"]
        if high_i == max(c["high"] for c in window):
            swings.append(SwingPoint(index=i, price=high_i, kind="high"))
        if low_i == min(c["low"] for c in window):
            swings.append(SwingPoint(index=i, price=low_i, kind="low"))
    return swings


def classify_bias(candles: list[dict]) -> Literal["bullish", "bearish", "ranging"]:
    """
    D1 bias from swing structure: a sequence of higher-highs and
    higher-lows across the most recent swing points -> "bullish";
    lower-highs and lower-lows -> "bearish"; anything else -> "ranging".
    """
    swings = find_swing_points(candles)
    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]

    if len(highs) < 2 or len(lows) < 2:
        return "ranging"

    higher_highs = highs[-1] > highs[-2]
    higher_lows = lows[-1] > lows[-2]
    lower_highs = highs[-1] < highs[-2]
    lower_lows = lows[-1] < lows[-2]

    if higher_highs and higher_lows:
        return "bullish"
    if lower_highs and lower_lows:
        return "bearish"
    return "ranging"


def _last_closed_candle(candles: list[dict]) -> Optional[dict]:
    """
    The M15 entry trigger must use only the last CLOSED candle, never the
    one still forming. Broker trendbar feeds return the forming candle as
    the last element, so this returns candles[-2].
    """
    if len(candles) < 2:
        return None
    return candles[-2]


def _classify_h4_setup(
    h4_candles: list[dict], d1_bias: Literal["bullish", "bearish", "ranging"]
) -> tuple[Optional[str], bool]:
    """
    Simplified ICT-style setup classification: compares the H4 structural
    bias against the D1 bias. Returns (setup_type, consistent_with_d1_bias).
    setup_type is "bos" (break of structure, continuation) if the H4
    structure agrees with the D1 bias, "choch" (change of character) if it
    disagrees, or None if H4 structure itself is ranging/inconclusive.
    """
    if d1_bias == "ranging":
        return None, False

    h4_bias = classify_bias(h4_candles)
    if h4_bias == "ranging":
        return None, False

    consistent = h4_bias == d1_bias
    setup_type = "bos" if consistent else "choch"
    return setup_type, consistent


def _classify_m15_trigger(last_closed: Optional[dict], direction_bias: str) -> Optional[str]:
    """
    Simplified M15 trigger classification: a closed candle whose body
    confirms the expected direction (bullish close for a bullish bias,
    bearish close for a bearish bias) is treated as a basic structure
    confirmation trigger.
    """
    if last_closed is None or direction_bias not in ("bullish", "bearish"):
        return None
    is_bullish_candle = last_closed["close"] > last_closed["open"]
    if direction_bias == "bullish" and is_bullish_candle:
        return "bullish_close_confirmation"
    if direction_bias == "bearish" and not is_bullish_candle:
        return "bearish_close_confirmation"
    return None


@dataclass
class StrategyContextResult:
    symbol: str
    d1_bias: str
    d1_swing_points: list[dict]
    h4_setup_type: Optional[str]
    h4_setup_valid: bool
    h4_consistent_with_d1_bias: bool
    m15_trigger_type: Optional[str]
    m15_last_closed_candle: Optional[dict]
    m15_candle_close_time: str
    raw_candle_counts: dict

    def to_json_dict(self) -> dict:
        return asdict(self)


async def build_strategy_context(
    ctrader_client, symbol: str, strategy_config: dict
) -> StrategyContextResult:
    """
    Fetches D1/H4/M15 trendbars via `ctrader_client.get_trendbars()`, with
    candle counts from `strategy_config["timeframes"]`, computes structure
    signals per timeframe, and assembles a single JSON-serializable context
    object handed to the LLM as the user-turn payload (prompt_system.md is
    the system prompt; this is the data).
    """
    tf_cfg = strategy_config["timeframes"]
    bias_cfg, setup_cfg, entry_cfg = tf_cfg["bias"], tf_cfg["setup"], tf_cfg["entry"]

    d1_candles = await ctrader_client.get_trendbars(symbol, bias_cfg["tf"], bias_cfg["candles"])
    h4_candles = await ctrader_client.get_trendbars(symbol, setup_cfg["tf"], setup_cfg["candles"])
    m15_candles = await ctrader_client.get_trendbars(symbol, entry_cfg["tf"], entry_cfg["candles"])

    d1_bias = classify_bias(d1_candles)
    d1_swings = find_swing_points(d1_candles)

    h4_setup_type, h4_consistent = _classify_h4_setup(h4_candles, d1_bias)
    h4_setup_valid = h4_setup_type is not None and h4_consistent

    last_closed = _last_closed_candle(m15_candles)
    m15_trigger_type = _classify_m15_trigger(last_closed, d1_bias) if h4_setup_valid else None

    close_time = (
        last_closed["timestamp"] if last_closed else datetime.now(timezone.utc).isoformat()
    )

    return StrategyContextResult(
        symbol=symbol,
        d1_bias=d1_bias,
        d1_swing_points=[asdict(s) for s in d1_swings],
        h4_setup_type=h4_setup_type,
        h4_setup_valid=h4_setup_valid,
        h4_consistent_with_d1_bias=h4_consistent,
        m15_trigger_type=m15_trigger_type,
        m15_last_closed_candle=last_closed,
        m15_candle_close_time=close_time,
        raw_candle_counts={
            bias_cfg["tf"]: len(d1_candles),
            setup_cfg["tf"]: len(h4_candles),
            entry_cfg["tf"]: len(m15_candles),
        },
    )
