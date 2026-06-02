#!/usr/bin/env python3
"""
Tradalgo Web Interface — Lance les backtests, visualise les charts, streame les logs.
Usage: python webapp.py   (puis http://localhost:5000)
"""
from __future__ import annotations
import io
import json
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).parent))

from tradalgo.data.loader import load_data
from tradalgo.data.preprocessor import preprocess
from tradalgo.indicators.ema import ema as _ema
from tradalgo.indicators.atr import atr as _atr
from tradalgo.smc.order_blocks import detect_order_blocks, update_mitigation
from tradalgo.reporting.metrics import compute_metrics

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# ── Shared state ───────────────────────────────────────────────────────────────

class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.log_lines: list[str] = []
        # Cache last loaded market data for charts
        self._mkt_cache: Optional[dict] = None
        self._mkt_key: Optional[str] = None

    def cache_mkt(self, key: str, data: dict):
        self._mkt_key = key
        self._mkt_cache = data

    def get_mkt(self, key: str) -> Optional[dict]:
        return self._mkt_cache if self._mkt_key == key else None


_state = _State()


# ── Stdout tee for log capture ─────────────────────────────────────────────────

class _Tee(io.TextIOBase):
    def __init__(self, orig, buf: list):
        self.orig = orig
        self.buf = buf

    def write(self, s: str) -> int:
        self.orig.write(s)
        self.orig.flush()
        stripped = s.rstrip("\n")
        if stripped:
            with _state.lock:
                _state.log_lines.append(stripped)
        return len(s)

    def flush(self):
        self.orig.flush()


# ── Backtest thread ────────────────────────────────────────────────────────────

def _run_backtest(params: dict) -> None:
    orig = sys.stdout
    sys.stdout = _Tee(orig, _state.log_lines)
    try:
        _do_backtest(params)
    except Exception as exc:
        tb = traceback.format_exc()
        with _state.lock:
            _state.error = f"{exc}\n\n{tb}"
            _state.running = False
        print(f"✗ Erreur: {exc}")
    finally:
        sys.stdout = orig


def _do_backtest(params: dict) -> None:
    from tradalgo.backtest.engine import BacktestConfig, BacktestEngine
    from tradalgo.backtest.swing_engine import SwingConfig, SwingEngine
    from tradalgo.backtest.mtf_engine import MTFConfig, MTFEngine

    mode = params.get("mode", "intraday")
    equity0 = float(params.get("equity", 10_000))

    print(f"▶ Backtest {mode.upper()} | {params.get('start')} → {params.get('end')} | ${equity0:,.0f}")

    if mode == "swing":
        cfg = SwingConfig(
            source=params.get("source", "yfinance"),
            csv_path=params.get("csv") or None,
            start_date=params.get("start", "2022-01-01"),
            end_date=params.get("end", "2025-01-01"),
            initial_equity=equity0,
            risk_pct=float(params.get("risk", 0.01)),
            spread_pips=float(params.get("spread", 1.5)),
            impulse_threshold=float(params.get("impulse", 1.5)),
            ob_lookback=int(params.get("ob_lookback", 800)),
            tp_rr=float(params.get("tp_rr", 6.0)),
            max_hold_days=int(params.get("max_hold", 180)),
            sizing_mode=params.get("sizing", "risk"),
            output_dir="output",
        )
        result = SwingEngine(cfg).run()

    elif mode == "mtf":
        cfg = MTFConfig(
            source=params.get("source", "histdata_csv"),
            csv_path=params.get("csv") or None,
            start_date=params.get("start", "2022-01-01"),
            end_date=params.get("end", "2025-01-01"),
            initial_equity=equity0,
            risk_pct=float(params.get("risk", 0.01)),
            spread_pips=float(params.get("spread", 1.5)),
            h4_ob_lookback=int(params.get("h4_ob_lookback", 200)),
            h4_fib_lookback=int(params.get("h4_fib_lookback", 120)),
            max_trades_per_day=int(params.get("max_trades_day", 3)),
            output_dir="output",
        )
        result = MTFEngine(cfg).run()
        print(f"  Execution TF: {result.exec_tf}")

    else:
        cfg = BacktestConfig(
            source=params.get("source", "yfinance"),
            csv_path=params.get("csv") or None,
            start_date=params.get("start", "2023-01-01"),
            end_date=params.get("end", "2025-01-01"),
            initial_equity=equity0,
            risk_pct=float(params.get("risk", 0.01)),
            spread_pips=float(params.get("spread", 1.5)),
            min_rr=float(params.get("min_rr", 2.0)),
            impulse_threshold=float(params.get("impulse", 1.5)),
            ob_lookback=int(params.get("ob_lookback", 800)),
            max_trades_per_day=int(params.get("max_trades_day", 2)),
            output_dir="output",
        )
        result = BacktestEngine(cfg).run()

    metrics = compute_metrics(result.trades, result.equity_curve, equity0)

    trades_out = []
    for t in result.trades:
        trades_out.append({
            "id": t.trade_id,
            "direction": t.direction,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "entry_price": round(t.entry_price, 5),
            "exit_price": round(t.exit_price, 5) if t.exit_price else None,
            "stop_loss": round(t.stop_loss, 5),
            "take_profit": round(t.take_profit, 5),
            "lot_size": t.lot_size,
            "exit_reason": t.exit_reason,
            "pnl_pips": t.pnl_pips,
            "pnl_usd": t.pnl_usd,
            "r_multiple": t.r_multiple,
            "equity_after": t.equity_after,
            "risk_usd": round(t.risk_usd, 2),
            "rr_ratio": t.rr_ratio,
        })

    equity_curve = [
        {"t": ts.isoformat(), "v": round(float(v), 2)}
        for ts, v in result.equity_curve.items()
    ]

    with _state.lock:
        _state.result = {
            "mode": mode,
            "params": params,
            "metrics": {
                "total_trades": metrics.total_trades,
                "win_rate": metrics.win_rate,
                "avg_rr": metrics.avg_rr,
                "profit_factor": metrics.profit_factor,
                "sharpe_ratio": metrics.sharpe_ratio,
                "max_drawdown_pct": metrics.max_drawdown_pct,
                "total_return_pct": metrics.total_return_pct,
                "avg_pnl_pips": metrics.avg_pnl_pips,
                "total_pnl_usd": metrics.total_pnl_usd,
            },
            "trades": trades_out,
            "equity_curve": equity_curve,
        }
        _state.running = False

    print(f"\n✓ Terminé — {len(trades_out)} trades | Retour: {metrics.total_return_pct}% | Sharpe: {metrics.sharpe_ratio}")


# ── Chart helpers ──────────────────────────────────────────────────────────────

def _ohlcv_to_list(df: pd.DataFrame, max_bars: int = 8000) -> list[dict]:
    if len(df) > max_bars:
        df = df.iloc[-max_bars:]
    out = []
    for ts, row in df.iterrows():
        out.append({
            "t": ts.isoformat(),
            "o": round(float(row["Open"]), 5),
            "h": round(float(row["High"]), 5),
            "l": round(float(row["Low"]), 5),
            "c": round(float(row["Close"]), 5),
            "v": int(row.get("Volume", 0)),
        })
    return out


def _series_to_list(s: pd.Series, max_bars: int = 8000) -> list[dict]:
    if len(s) > max_bars:
        s = s.iloc[-max_bars:]
    return [
        {"t": ts.isoformat(), "v": round(float(v), 5)}
        for ts, v in s.items()
        if not (isinstance(v, float) and np.isnan(v))
    ]


def _compute_obs_with_mitigation(
    df: pd.DataFrame,
    atr_s: pd.Series,
    impulse_bars: int,
    impulse_threshold: float,
    chart_start: Optional[pd.Timestamp] = None,
    max_obs: int = 400,
    max_age_months: int = 24,
) -> list[dict]:
    """
    Detect OBs, simulate full mitigation history, then return only the OBs
    that formed within the chart window (chart_start - 3 months).

    Each OB rectangle ends at its mitigation bar — not at end of chart —
    so mitigated OBs appear as short, historically accurate segments.
    """
    obs = detect_order_blocks(df, atr_s, impulse_bars, impulse_threshold)
    for i in range(len(df)):
        bar = df.iloc[i]
        for _dir in ("bullish", "bearish"):
            update_mitigation(obs, bar, _dir, i)

    # Age filter: OBs older than max_age_months from the last bar are irrelevant
    # (e.g. bearish OBs at 1.22 from 2021 when price is now at 1.08)
    last_bar_ts = df.index[-1]
    age_cutoff = last_bar_ts - pd.DateOffset(months=max_age_months)
    if age_cutoff.tzinfo is None:
        age_cutoff = age_cutoff.tz_localize("UTC")
    obs = [ob for ob in obs if df.index[ob.bar_idx] >= age_cutoff]

    # Also skip deep warmup OBs that formed before the backtest start window
    if chart_start is not None:
        cutoff = chart_start - pd.DateOffset(months=3)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize("UTC")
        obs = [ob for ob in obs if df.index[ob.bar_idx] >= cutoff]

    # Keep only the most recent max_obs (avoids JSON bloat on H1/M15)
    if len(obs) > max_obs:
        obs = sorted(obs, key=lambda o: o.bar_idx, reverse=True)[:max_obs]

    last_ts = df.index[-1].isoformat()
    out = []
    for ob in obs:
        if ob.mitigation_bar_idx >= 0:
            mit_idx = min(ob.mitigation_bar_idx, len(df) - 1)
            end_ts = df.index[mit_idx].isoformat()
        else:
            end_ts = last_ts

        out.append({
            "direction": ob.direction,
            "zone_low":  round(ob.zone_low,  5),
            "zone_high": round(ob.zone_high, 5),
            "start_time": df.index[ob.bar_idx].isoformat(),
            "conf_time":  df.index[min(ob.confirmation_bar_idx, len(df) - 1)].isoformat(),
            "end_time":   end_ts,
            "mitigated":  ob.is_mitigated,
        })
    return out


def _load_market_data(params: dict):
    """Load and preprocess market data, with in-memory cache."""
    key = json.dumps({k: params.get(k) for k in ("source", "csv", "start", "end")}, sort_keys=True)
    cached = _state.get_mkt(key)
    if cached:
        return cached

    mkt = load_data(
        source=params.get("source", "yfinance"),
        csv_path=params.get("csv") or None,
        start=params.get("start", "2023-01-01"),
        end=params.get("end", "2025-01-01"),
        cache_dir="data_cache",
    )
    h1, d1 = preprocess(mkt.h1, mkt.d1)

    h4 = h1.resample("4h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Open"])

    from tradalgo.strategy.trend_w1 import resample_weekly
    w1 = resample_weekly(d1)

    result = {"h1": h1, "d1": d1, "h4": h4, "w1": w1, "m15": mkt.m15}
    _state.cache_mkt(key, result)
    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run-backtest", methods=["POST"])
def run_backtest():
    with _state.lock:
        if _state.running:
            return jsonify({"error": "Un backtest est déjà en cours"}), 409
        _state.running = True
        _state.result = None
        _state.error = None
        _state.log_lines = []

    params = request.json or {}
    t = threading.Thread(target=_run_backtest, args=(params,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/status")
def status():
    with _state.lock:
        return jsonify({
            "running": _state.running,
            "has_result": _state.result is not None,
            "error": _state.error,
            "log_count": len(_state.log_lines),
        })


@app.route("/api/results")
def results():
    with _state.lock:
        if _state.result is None:
            return jsonify({"error": "Aucun résultat"}), 404
        return jsonify(_state.result)


@app.route("/api/logs")
def logs():
    offset = int(request.args.get("offset", 0))
    with _state.lock:
        lines = _state.log_lines[offset:]
        total = len(_state.log_lines)
    return jsonify({"lines": lines, "total": total})


@app.route("/api/chart-data/<tf>")
def chart_data(tf: str):
    with _state.lock:
        if _state.result is None:
            return jsonify({"error": "Lance un backtest d'abord"}), 404
        params = _state.result["params"]
        trades = _state.result["trades"]

    try:
        mkt = _load_market_data(params)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    impulse_thr  = float(params.get("impulse", 1.5))
    chart_start  = pd.Timestamp(params.get("start", "2022-01-01")).tz_localize("UTC")

    # Per-TF config: (impulse_bars, max_obs, max_ohlcv_bars, max_age_months)
    TF_CFG = {
        "w1":  (2, 150,  None, 48),
        "d1":  (3, 200,  None, 24),
        "h4":  (3, 300,  None, 12),   # impulse_bars 3 (was 2) — reduces false OBs
        "h1":  (3, 500,  8000,  6),
        "m15": (3, 600,  5000,  3),
    }
    if tf not in TF_CFG:
        return jsonify({"error": f"TF inconnu: {tf}"}), 400
    imp_bars, max_obs, max_bars, max_age = TF_CFG[tf]

    try:
        if tf == "w1":
            df = mkt["w1"]
            atr_s = _atr(df, 14)
            return jsonify({
                "tf": "W1", "ohlcv": _ohlcv_to_list(df),
                "emas": [
                    {"name": "EMA20", "color": "#00bfff", "values": _series_to_list(_ema(df["Close"], 20))},
                    {"name": "EMA50", "color": "#ff8c00", "values": _series_to_list(_ema(df["Close"], 50))},
                ],
                "obs": _compute_obs_with_mitigation(df, atr_s, imp_bars, impulse_thr, chart_start, max_obs, max_age),
                "trades": trades,
            })

        elif tf == "d1":
            df = mkt["d1"]
            atr_s = _atr(df, 14)
            return jsonify({
                "tf": "D1", "ohlcv": _ohlcv_to_list(df),
                "emas": [
                    {"name": "EMA50", "color": "#00bfff", "values": _series_to_list(_ema(df["Close"], 50))},
                    {"name": "EMA200", "color": "#ff8c00", "values": _series_to_list(_ema(df["Close"], 200))},
                ],
                "obs": _compute_obs_with_mitigation(df, atr_s, imp_bars, impulse_thr, chart_start, max_obs, max_age),
                "trades": trades,
            })

        elif tf == "h4":
            df = mkt["h4"]
            atr_s = _atr(df, 14)
            return jsonify({
                "tf": "H4", "ohlcv": _ohlcv_to_list(df),
                "emas": [],
                "obs": _compute_obs_with_mitigation(df, atr_s, imp_bars, impulse_thr, chart_start, max_obs, max_age),
                "trades": trades,
            })

        elif tf == "h1":
            df = mkt["h1"]
            atr_s = _atr(df, 14)
            return jsonify({
                "tf": "H1", "ohlcv": _ohlcv_to_list(df, max_bars=max_bars),
                "emas": [
                    {"name": "EMA50", "color": "#00bfff", "values": _series_to_list(_ema(df["Close"], 50))},
                    {"name": "EMA200", "color": "#ff8c00", "values": _series_to_list(_ema(df["Close"], 200))},
                ],
                "obs": _compute_obs_with_mitigation(df, atr_s, imp_bars, impulse_thr, chart_start, max_obs, max_age),
                "trades": trades,
            })

        elif tf == "m15":
            df = mkt.get("m15")
            if df is None or df.empty:
                return jsonify({"error": "Pas de données M15 (nécessite source M1 CSV)"}), 404
            atr_s = _atr(df, 14)
            return jsonify({
                "tf": "M15", "ohlcv": _ohlcv_to_list(df, max_bars=max_bars),
                "emas": [],
                "obs": _compute_obs_with_mitigation(df, atr_s, imp_bars, impulse_thr, chart_start, max_obs, max_age),
                "trades": trades,
            })

        else:
            return jsonify({"error": f"TF inconnu: {tf}"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/git-pull", methods=["POST"])
def git_pull():
    branch = request.json.get("branch", "claude/eurusd-trading-algo-T7p4x") if request.json else "claude/eurusd-trading-algo-T7p4x"
    try:
        r = subprocess.run(
            ["git", "pull", "origin", branch],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent), timeout=30,
        )
        return jsonify({
            "stdout": r.stdout, "stderr": r.stderr,
            "returncode": r.returncode, "success": r.returncode == 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    args = p.parse_args()
    print(f"Tradalgo Web  →  http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
