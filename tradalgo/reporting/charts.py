from __future__ import annotations
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def plot_equity_curve(
    equity_curve: pd.Series,
    output_path: str,
    title: str = "EURUSD SMC/ICT + Fibonacci — Backtest",
) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if equity_curve.empty:
        print(f"No equity data to chart — skipping {output_path}")
        return

    roll_max = equity_curve.cummax()
    drawdown = ((equity_curve - roll_max) / roll_max) * 100

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    fig.suptitle(title, fontsize=13, fontweight="bold")

    # Equity curve
    ax1.plot(equity_curve.index, equity_curve.values, color="#1565C0", linewidth=1.2)
    ax1.fill_between(equity_curve.index, equity_curve.values,
                     equity_curve.iloc[0], alpha=0.08, color="#1565C0")
    ax1.axhline(equity_curve.iloc[0], color="grey", linewidth=0.7, linestyle="--")
    ax1.set_ylabel("Equity (USD)")
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax1.grid(True, alpha=0.25)

    # Drawdown
    ax2.fill_between(drawdown.index, drawdown.values, 0, color="#C62828", alpha=0.55)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.grid(True, alpha=0.25)

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Chart saved → {output_path}")
