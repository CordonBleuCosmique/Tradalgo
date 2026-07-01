"""
decision_log/db.py

SQLite persistence layer for TradAlgo: the trade-decision audit trail plus
small runtime-config tables used by the dashboard (hot-swappable MCP token,
scheduler heartbeat/pause state).

Every function takes `db_path` explicitly (no module-level global
connection) so tests and multiple processes (agent, scheduler, dashboard)
can all point at the same on-disk file, or tests can point at a temp file.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults (row factory, FKs)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """Create tables/indexes if they do not exist. Idempotent."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        with open(SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


def insert_decision(
    db_path: str,
    *,
    m15_candle_close_time: str,
    symbol: str,
    d1_bias: Optional[str],
    h4_setup_valid: Optional[bool],
    h4_setup_type: Optional[str],
    m15_trigger_type: Optional[str],
    market_context: dict,
    llm_raw_response: str,
    decision: str,
    size: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
    guardrail_status: str,
    guardrail_reason: Optional[str],
    executed: bool,
    processing_latency_ms: Optional[int],
    result_pnl: Optional[float] = None,
) -> int:
    """Insert one decision record. Returns the inserted row id."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO decisions (
                timestamp, m15_candle_close_time, symbol, d1_bias,
                h4_setup_valid, h4_setup_type, m15_trigger_type,
                market_context_json, llm_raw_response, decision, size,
                stop_loss, take_profit, guardrail_status, guardrail_reason,
                executed, result_pnl, processing_latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(),
                m15_candle_close_time,
                symbol,
                d1_bias,
                h4_setup_valid,
                h4_setup_type,
                m15_trigger_type,
                json.dumps(market_context),
                llm_raw_response,
                decision,
                size,
                stop_loss,
                take_profit,
                guardrail_status,
                guardrail_reason,
                int(executed),
                result_pnl,
                processing_latency_ms,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_last_n_decisions(db_path: str, n: int = 5) -> list[dict]:
    """Most recent N decisions, newest first, as plain dicts (JSON-decoded market_context)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["market_context_json"] = json.loads(d["market_context_json"])
            except (TypeError, json.JSONDecodeError):
                pass
            result.append(d)
        return result
    finally:
        conn.close()


def count_trades_today(db_path: str, *, as_of: Optional[datetime] = None) -> int:
    """Count rows where executed=1 and timestamp falls on the current UTC calendar day."""
    day = (as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM decisions WHERE executed = 1 AND substr(timestamp, 1, 10) = ?",
            (day,),
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def compute_daily_drawdown_pct(
    db_path: str, *, starting_balance: float, as_of: Optional[datetime] = None
) -> float:
    """
    Sum result_pnl for executed=1 rows on the current UTC day, express as a
    percentage of starting_balance. Returns 0.0 if no closed trades yet
    today, or if starting_balance is 0 (avoid division by zero).
    """
    if not starting_balance:
        return 0.0
    day = (as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(result_pnl), 0) AS total_pnl
            FROM decisions
            WHERE executed = 1 AND result_pnl IS NOT NULL AND substr(timestamp, 1, 10) = ?
            """,
            (day,),
        ).fetchone()
        total_pnl = row["total_pnl"] if row else 0.0
        return (total_pnl / starting_balance) * 100.0
    finally:
        conn.close()


def update_scheduler_heartbeat(
    db_path: str,
    *,
    latency_ms: Optional[int] = None,
    decision: Optional[str] = None,
    token_health: Optional[str] = None,
) -> None:
    """Upsert the singleton scheduler_state row (id=1)."""
    conn = get_connection(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO scheduler_state (id) VALUES (1)")
        sets = ["last_heartbeat = ?"]
        params: list[Any] = [_now_iso()]
        if latency_ms is not None:
            sets.append("last_cycle_timestamp = ?")
            params.append(_now_iso())
            sets.append("last_cycle_latency_ms = ?")
            params.append(latency_ms)
        if decision is not None:
            sets.append("last_cycle_decision = ?")
            params.append(decision)
        if token_health is not None:
            sets.append("last_token_health = ?")
            params.append(token_health)
        conn.execute(f"UPDATE scheduler_state SET {', '.join(sets)} WHERE id = 1", params)
        conn.commit()
    finally:
        conn.close()


def get_scheduler_state(db_path: str) -> dict:
    """Read the singleton scheduler_state row as a dict."""
    conn = get_connection(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO scheduler_state (id) VALUES (1)")
        conn.commit()
        row = conn.execute("SELECT * FROM scheduler_state WHERE id = 1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def set_scheduler_paused(db_path: str, paused: bool) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO scheduler_state (id) VALUES (1)")
        conn.execute("UPDATE scheduler_state SET paused = ? WHERE id = 1", (int(paused),))
        conn.commit()
    finally:
        conn.close()


def get_app_config(db_path: str, key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_app_config(db_path: str, key: str, value: str) -> None:
    """Upsert into app_config with updated_at = now (UTC ISO8601)."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
