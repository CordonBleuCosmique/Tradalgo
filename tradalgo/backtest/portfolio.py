from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class TradeRecord:
    trade_id: int
    direction: str
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp]
    entry_price: float
    exit_price: Optional[float]
    stop_loss: float
    take_profit: float
    lot_size: float
    exit_reason: str        # "tp_hit" | "sl_hit" | "eod_close" | "open"
    pnl_pips: float = 0.0
    pnl_usd: float = 0.0
    r_multiple: float = 0.0
    equity_after: float = 0.0
    risk_usd: float = 0.0
    rr_ratio: float = 0.0


class Portfolio:
    PIP = 0.0001
    PIP_VALUE_USD = 10.0

    def __init__(self, initial_equity: float):
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.trades: list[TradeRecord] = []
        self.open_trade: Optional[TradeRecord] = None
        self._counter = 0

    def next_trade_id(self) -> int:
        self._counter += 1
        return self._counter

    def open(self, trade: TradeRecord) -> None:
        self.open_trade = trade

    def close(
        self,
        exit_price: float,
        exit_time: pd.Timestamp,
        exit_reason: str,
    ) -> TradeRecord:
        t = self.open_trade
        assert t is not None, "No open trade to close"

        if t.direction == "bullish":
            pnl_pips = (exit_price - t.entry_price) / self.PIP
        else:
            pnl_pips = (t.entry_price - exit_price) / self.PIP

        pnl_usd = pnl_pips * self.PIP_VALUE_USD * t.lot_size
        self.equity += pnl_usd
        r_multiple = pnl_usd / t.risk_usd if t.risk_usd != 0 else 0.0

        t.exit_price = exit_price
        t.exit_time = exit_time
        t.exit_reason = exit_reason
        t.pnl_pips = round(pnl_pips, 1)
        t.pnl_usd = round(pnl_usd, 2)
        t.r_multiple = round(r_multiple, 2)
        t.equity_after = round(self.equity, 2)

        self.trades.append(t)
        self.open_trade = None
        return t
