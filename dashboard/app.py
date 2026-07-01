"""
dashboard/app.py

Lightweight Flask monitoring/reconnection dashboard for TradAlgo.

GET / is read-only and MUST render successfully even when the cTrader MCP
token is invalid or missing -- every broker call is wrapped so the page
degrades gracefully instead of failing to load. The mutating routes
(/reconnect, /pause, /resume) are gated behind a minimal HMAC-signed-cookie
auth pattern (single shared password from .env) -- not a full auth
framework, per spec.
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import urllib.request
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for

from broker.ctrader_client import CTraderClient
from decision_log import db as decision_db

load_dotenv()

app = Flask(__name__)

DB_PATH = os.environ.get("TRADALGO_DB_PATH", "decision_log/tradalgo.db")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", "")
ALERT_WEBHOOK_URL = os.environ.get("TRADALGO_ALERT_WEBHOOK_URL") or None
SESSION_COOKIE_NAME = "tradalgo_session"
SESSION_TTL_SECONDS = 12 * 3600

decision_db.init_db(DB_PATH)


def _make_session_cookie() -> str:
    """HMAC-signed "<expiry>.<hex signature>" cookie value -- no session
    store, no Flask-Login, matches the spec's "lightweight, not a full
    auth system" requirement."""
    expiry = str(int(time.time()) + SESSION_TTL_SECONDS)
    sig = hmac.new(DASHBOARD_SECRET_KEY.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def _verify_session_cookie(cookie_value: str) -> bool:
    try:
        expiry_str, sig = cookie_value.split(".", 1)
    except ValueError:
        return False
    if int(expiry_str) < time.time():
        return False
    expected = hmac.new(DASHBOARD_SECRET_KEY.encode(), expiry_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def require_auth(view_func):
    """Applied only to the state-mutating routes (/reconnect, /pause,
    /resume). The read-only dashboard (GET /) is intentionally NOT gated --
    the spec asks for auth "in front of the reconnect functionality", not
    the whole read-only view."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
        if not DASHBOARD_SECRET_KEY or not _verify_session_cookie(cookie):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def _build_ctrader_client() -> CTraderClient:
    token = decision_db.get_app_config(DB_PATH, "ctrader_mcp_token") or os.environ.get(
        "CTRADER_MCP_TOKEN", ""
    )
    return CTraderClient(
        server_url=os.environ.get("CTRADER_MCP_SERVER_URL", ""),
        bearer_token=token,
    )


def notify_token_expired(webhook_url: str | None) -> None:
    """
    Best-effort webhook/notification hook, fired when the token flips
    healthy -> unhealthy. Optional: no-op if TRADALGO_ALERT_WEBHOOK_URL is
    unset. Uses stdlib urllib rather than adding a `requests` dependency
    for this rarely-hit code path. Never raises.
    """
    if not webhook_url:
        return
    try:
        data = json.dumps({"text": "TradAlgo: cTrader MCP token is unhealthy/expired."}).encode()
        req = urllib.request.Request(
            webhook_url, data=data, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


@app.route("/", methods=["GET"])
def index():
    scheduler_state = decision_db.get_scheduler_state(DB_PATH)
    decisions = decision_db.fetch_last_n_decisions(DB_PATH, n=50)

    cumulative = 0.0
    pnl_series = []
    for d in reversed(decisions):
        if d.get("result_pnl") is not None:
            cumulative += d["result_pnl"]
        pnl_series.append({"timestamp": d["timestamp"], "cumulative_pnl": cumulative})

    account_info = {"balance": None, "equity": None, "positions": None, "error": None}
    try:
        client = _build_ctrader_client()

        async def fetch():
            balance = await client.get_balance()
            positions = await client.get_positions()
            return balance, positions

        balance, positions = asyncio.run(fetch())
        account_info["balance"] = balance.get("balance")
        account_info["equity"] = balance.get("equity")
        account_info["positions"] = positions
    except Exception as e:
        account_info["error"] = "Account data unavailable (MCP token invalid or unreachable)."

    token_status = scheduler_state.get("last_token_health", "UNKNOWN")
    if token_status == "EXPIRED":
        notify_token_expired(ALERT_WEBHOOK_URL)

    return render_template(
        "index.html",
        token_status=token_status,
        scheduler_state=scheduler_state,
        account_info=account_info,
        decisions=decisions,
        pnl_json=json.dumps(pnl_series),
        dashboard_port=os.environ.get("DASHBOARD_PORT", "8002"),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if DASHBOARD_PASSWORD and hmac.compare_digest(submitted, DASHBOARD_PASSWORD):
            response = redirect(url_for("index"))
            response.set_cookie(
                SESSION_COOKIE_NAME, _make_session_cookie(), httponly=True, max_age=SESSION_TTL_SECONDS
            )
            return response
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/reconnect", methods=["POST"])
@require_auth
def reconnect():
    new_token = request.form.get("new_token", "").strip()
    if new_token:
        decision_db.set_app_config(DB_PATH, "ctrader_mcp_token", new_token)
        client = CTraderClient(server_url=os.environ.get("CTRADER_MCP_SERVER_URL", ""), bearer_token=new_token)
        healthy = asyncio.run(client.check_token_health())
        decision_db.update_scheduler_heartbeat(DB_PATH, token_health="OK" if healthy else "EXPIRED")
    return redirect(url_for("index"))


@app.route("/pause", methods=["POST"])
@require_auth
def pause():
    decision_db.set_scheduler_paused(DB_PATH, True)
    return redirect(url_for("index"))


@app.route("/resume", methods=["POST"])
@require_auth
def resume():
    decision_db.set_scheduler_paused(DB_PATH, False)
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "8002"))
    app.run(host="0.0.0.0", port=port)
