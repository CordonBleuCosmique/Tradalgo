from __future__ import annotations
import csv
from pathlib import Path

from tradalgo.backtest.portfolio import TradeRecord

_FIELDS = [
    "trade_id", "direction", "entry_time", "exit_time",
    "entry_price", "exit_price", "stop_loss", "take_profit",
    "lot_size", "exit_reason", "pnl_pips", "pnl_usd",
    "r_multiple", "equity_after", "risk_usd", "rr_ratio",
]


def write_trade_log(trades: list[TradeRecord], output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "trade_id":    t.trade_id,
                "direction":   t.direction,
                "entry_time":  t.entry_time,
                "exit_time":   t.exit_time,
                "entry_price": round(t.entry_price, 5),
                "exit_price":  round(t.exit_price, 5) if t.exit_price else "",
                "stop_loss":   round(t.stop_loss, 5),
                "take_profit": round(t.take_profit, 5),
                "lot_size":    t.lot_size,
                "exit_reason": t.exit_reason,
                "pnl_pips":    t.pnl_pips,
                "pnl_usd":     t.pnl_usd,
                "r_multiple":  t.r_multiple,
                "equity_after":t.equity_after,
                "risk_usd":    t.risk_usd,
                "rr_ratio":    t.rr_ratio,
            })
