CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    m15_candle_close_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    d1_bias TEXT,
    h4_setup_valid BOOLEAN,
    h4_setup_type TEXT,
    m15_trigger_type TEXT,
    market_context_json TEXT NOT NULL,
    llm_raw_response TEXT NOT NULL,
    decision TEXT NOT NULL,
    size REAL,
    stop_loss REAL,
    take_profit REAL,
    guardrail_status TEXT NOT NULL,
    guardrail_reason TEXT,
    executed BOOLEAN NOT NULL DEFAULT 0,
    result_pnl REAL,
    processing_latency_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);

-- Dashboard hot-swappable runtime config (e.g. token reconnect without a
-- service restart). Simple singleton-row-per-key table.
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

-- Scheduler heartbeat / pause-resume control surface for the dashboard.
CREATE TABLE IF NOT EXISTS scheduler_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    paused BOOLEAN NOT NULL DEFAULT 0,
    last_heartbeat TEXT,
    last_cycle_timestamp TEXT,
    last_cycle_latency_ms INTEGER,
    last_cycle_decision TEXT,
    last_token_health TEXT DEFAULT 'UNKNOWN'
);
INSERT OR IGNORE INTO scheduler_state (id, paused, last_token_health) VALUES (1, 0, 'UNKNOWN');
