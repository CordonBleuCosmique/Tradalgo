# TradAlgo

Experimental, full-auto LLM-driven trading agent: Claude decides trades on
its own, via MCP access to cTrader, on a **DEMO account only**. This is a
deliberate inversion of the "AI never generates signals" philosophy used
elsewhere (InvestAlgo) — the goal here is to observe whether an edge
emerges when the LLM decides alone, with full logging for post-mortem
analysis.

Instruments: XAUUSD, EURUSD. Designed to run as a systemd daemon on a
Raspberry Pi / mini-PC, alongside InvestAlgo (port 8001) and ClubInvest
(port 8000).

## Module layout notes

Two directories from the original spec were renamed to avoid Python import
collisions:

- `mcp/` → **`broker/`** — a local top-level package literally named `mcp`
  would shadow the third-party `mcp` PyPI SDK (the official MCP client
  library) under normal `sys.path` resolution, since the repo root is
  searched before `site-packages`.
- `logging/` → **`decision_log/`** — a local top-level package named
  `logging` would shadow Python's stdlib `logging` module, and not just
  for this project's own code: Flask and the `anthropic` SDK both do
  `import logging` internally, and module name resolution is global to the
  process (`sys.modules` is a single shared cache), so their internal
  imports would silently receive our package instead of the real stdlib
  facility.

Because of these two renames, **no special import discipline is needed
anywhere in this codebase** — every import is a standard Python import,
including `import logging` (stdlib) if any file needs it for its own
operational logs. The trade-decision audit trail (the actually important
"log" in this domain) lives in `decision_log/db.py` (SQLite), separate
from Python's stdlib logging facility.

All other file names, the SQL schema, the 6 MCP tool names, and the YAML
config keys follow the original spec verbatim.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`:

- `ANTHROPIC_API_KEY` — your Anthropic API key.
- **cTrader Remote MCP bearer token**: log in to cTrader Web with your
  **DEMO** account → Settings → Remote MCP → generate a token. This token
  is tied to your active Web session and can expire; there is no silent
  auto-retry in this app — you'll see one clear log line and the cycle
  will be cancelled (no trade) until you regenerate it, either by editing
  `.env` or via the dashboard's Reconnect form.
- `CTRADER_MCP_SERVER_URL` — copy the exact Remote MCP endpoint URL from
  that same cTrader Web page. This is **not hardcoded anywhere** in this
  codebase by design (no confirmed exact endpoint was available at
  scaffold time) — you must supply it.
- `DASHBOARD_PASSWORD` / `DASHBOARD_SECRET_KEY` — pick any values; these
  gate the dashboard's reconnect/pause/resume controls.

## Configuring `config/strategy.yaml`

- `timeframes.bias/setup/entry` — the D1/H4/M15 candle counts fetched each
  cycle for bias, setup, and entry-trigger analysis respectively.
- `entry_trigger: on_m15_close` — the entry trigger is always evaluated on
  the last **closed** M15 candle, never the one still forming.
- `scheduler.latency_buffer_seconds` — how long past the exact M15 close
  the scheduler waits before triggering a cycle, so the broker has time to
  finalize/publish the candle.
- `scheduler.max_processing_time_seconds` — if a cycle's processing latency
  exceeds this, the scheduler logs a warning (it doesn't abort the cycle,
  it flags drift for you to investigate).

`config/guardrails.yaml` holds the hard-coded, non-negotiable safety limits
(demo-mode requirement, max position size, mandatory stop loss, daily
drawdown cap, max trades/day, symbol whitelist) — these are enforced in
code by `core/guardrails.py`, not by the LLM, before every single order.

## Running tests

```bash
pytest
```

Both `tests/test_guardrails.py` and `tests/test_scheduler.py` run fully
offline (no network, no broker, no Claude API calls).

## Starting the dashboard

```bash
python -m dashboard.app
```

Visit `http://localhost:8002`. The read-only view (status, balance,
positions, decision history, cumulative PnL) works even if the MCP token
is invalid or missing — it degrades gracefully rather than failing to
load. Log in (via the link in the header) to reconnect a new token or
pause/resume the daemon.

## Running one manual dry-run cycle

```bash
python -m core.agent --dry-run --once
```

This runs a single decision cycle: checks the MCP token, builds the
D1→H4→M15 context, calls Claude, runs the result through the guardrails,
and logs everything to `decision_log/tradalgo.db` — but never places an
order (guardrail-approved decisions are logged as "would execute", not
sent to the executor).

**Run this manually, multiple times, against real M15 closes, and inspect
the logged decisions (via the dashboard or directly in SQLite) before
enabling the continuous daemon below.**

## Enabling the systemd daemon (manual, last step)

`scheduler/tradalgo.service` is provided as a plain file — **it is not
installed, enabled, or started by anything in this repo.** To use it on
the Pi:

```bash
sudo cp scheduler/tradalgo.service /etc/systemd/system/
# edit User=, WorkingDirectory=, and the .venv path to match your install
sudo systemctl daemon-reload
```

**Do not `systemctl enable`/`start` this until:**
1. You've run several successful `--dry-run --once` cycles and inspected
   their logged decisions in the dashboard.
2. The dashboard shows MCP token status = `OK`.

This project only talks to a DEMO account by guardrail design, but you are
still responsible for verifying it behaves as expected before letting it
run unattended.
