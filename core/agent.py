"""
core/agent.py

TradAlgo's decision-loop entrypoint.

    python -m core.agent --dry-run --once

Each cycle: gate on MCP token health -> build D1/H4/M15 context per
allowed symbol -> fetch open positions -> pull recent decisions for
continuity -> call Claude -> parse its structured JSON decision -> run it
through core/guardrails.py -> execute (or, in --dry-run, just log what
would have executed) -> always log the outcome to decision_log/db.py.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

import anthropic
import yaml
from dotenv import load_dotenv

from broker.ctrader_client import CTraderClient
from core import executor, guardrails, strategy_context
from decision_log import db as decision_db

DEFAULT_MODEL = "claude-sonnet-4-6"


def load_env_and_config(db_path_override: str | None = None) -> dict:
    """
    Loads .env, the two YAML config files, and constructs a CTraderClient.
    The MCP bearer token is read from .env by default, but decision_log's
    app_config table (written by the dashboard's /reconnect form) takes
    precedence if present -- this is how a hot-swapped token is picked up
    without restarting the service.
    """
    load_dotenv()

    guardrail_config = guardrails.load_guardrail_config("config/guardrails.yaml")
    with open("config/strategy.yaml", "r") as f:
        strategy_config = yaml.safe_load(f)

    db_path = db_path_override or os.environ.get("TRADALGO_DB_PATH", "decision_log/tradalgo.db")
    decision_db.init_db(db_path)

    token = decision_db.get_app_config(db_path, "ctrader_mcp_token") or os.environ.get(
        "CTRADER_MCP_TOKEN", ""
    )

    return {
        "db_path": db_path,
        "guardrail_config": guardrail_config,
        "strategy_config": strategy_config,
        "server_url": os.environ.get("CTRADER_MCP_SERVER_URL", ""),
        "token": token,
        "ctrader_environment": os.environ.get("CTRADER_ENVIRONMENT", "demo"),
        "model": os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        "anthropic_client": anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", "")),
        "system_prompt": open("core/prompt_system.md").read(),
    }


def build_prompt_messages(
    strategy_ctx: dict, open_positions: list[dict], recent_decisions: list[dict]
) -> list[dict]:
    """
    A single user-turn message containing the full JSON payload the model
    needs. `prompt_system.md` is passed separately as the `system`
    parameter -- not inlined here. No growing conversation history across
    cycles: continuity comes from `recent_decisions`, not from replaying
    prior turns, which keeps token usage bounded for a long-running daemon.
    """
    payload = {
        "market_context": strategy_ctx,
        "open_positions": open_positions,
        "recent_decisions": recent_decisions,
    }
    return [
        {
            "role": "user",
            "content": (
                "Here is the current market context, open positions, and recent "
                "decision history. Respond with ONLY the required JSON object.\n\n"
                + json.dumps(payload, indent=2, default=str)
            ),
        }
    ]


def _extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from the model's text response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def call_claude(
    client: "anthropic.Anthropic", model: str, system_prompt: str, messages: list[dict]
) -> tuple[dict, str]:
    """
    Calls the Anthropic Messages API and parses the first text block as
    JSON. On a parse failure, retries ONCE with a corrective follow-up
    message -- a single bounded retry, never an infinite loop, consistent
    with this project's "no silent negotiation" philosophy. Returns
    (parsed_dict, raw_response_text); the raw text is always preserved for
    the llm_raw_response DB column regardless of parse outcome.
    """
    response = client.messages.create(
        model=model, max_tokens=1024, system=system_prompt, messages=messages
    )
    raw_text = "".join(block.text for block in response.content if block.type == "text")

    try:
        return _extract_json(raw_text), raw_text
    except json.JSONDecodeError:
        pass

    retry_messages = messages + [
        {"role": "assistant", "content": raw_text},
        {
            "role": "user",
            "content": (
                "Your previous response was not valid JSON. Respond with ONLY "
                "the JSON object per the required schema, no other text."
            ),
        },
    ]
    response = client.messages.create(
        model=model, max_tokens=1024, system=system_prompt, messages=retry_messages
    )
    raw_text_retry = "".join(block.text for block in response.content if block.type == "text")
    return _extract_json(raw_text_retry), raw_text_retry


async def run_cycle(*, dry_run: bool, db_path_override: str | None = None) -> None:
    cycle_start = time.monotonic()
    env = load_env_and_config(db_path_override)
    db_path = env["db_path"]

    ctrader = CTraderClient(server_url=env["server_url"], bearer_token=env["token"])

    # 1. Token health gate -- BEFORE any multi-TF analysis. No silent retry.
    healthy = await ctrader.check_token_health()
    decision_db.update_scheduler_heartbeat(db_path, token_health="OK" if healthy else "EXPIRED")
    if not healthy:
        print(
            "[agent] MCP token unhealthy -- cancelling cycle. No decision, no trade. "
            "Regenerate the token via cTrader Web -> Settings -> Remote MCP, then "
            "update it via .env or the dashboard's Reconnect form.",
            file=sys.stderr,
        )
        return

    for symbol in env["guardrail_config"]["allowed_symbols"]:
        # 2. Multi-timeframe context (D1 -> H4 -> M15, last CLOSED M15 candle).
        ctx = await strategy_context.build_strategy_context(ctrader, symbol, env["strategy_config"])

        # 3. Open positions.
        positions = await ctrader.get_positions()

        # 4. Recent decisions, for continuity.
        recent = decision_db.fetch_last_n_decisions(db_path, n=5)

        # 5. Call Claude.
        messages = build_prompt_messages(ctx.to_json_dict(), positions, recent)
        latency_ms = int((time.monotonic() - cycle_start) * 1000)
        try:
            parsed, raw = call_claude(env["anthropic_client"], env["model"], env["system_prompt"], messages)
        except (json.JSONDecodeError, anthropic.APIError) as e:
            decision_db.insert_decision(
                db_path,
                m15_candle_close_time=ctx.m15_candle_close_time,
                symbol=symbol,
                d1_bias=ctx.d1_bias,
                h4_setup_valid=ctx.h4_setup_valid,
                h4_setup_type=ctx.h4_setup_type,
                m15_trigger_type=ctx.m15_trigger_type,
                market_context=ctx.to_json_dict(),
                llm_raw_response=str(e),
                decision="ERROR",
                size=None,
                stop_loss=None,
                take_profit=None,
                guardrail_status="REJECTED",
                guardrail_reason=f"LLM call/parse failure: {e}",
                executed=False,
                processing_latency_ms=int((time.monotonic() - cycle_start) * 1000),
            )
            continue

        # 6. Fresh account state + daily stats, then guardrails -- checked
        # every cycle, never cached, never just at startup.
        account_raw = await ctrader.get_balance()
        account = guardrails.AccountState(
            is_demo=(env["ctrader_environment"] == "demo"),
            balance=account_raw.get("balance", 0.0),
            equity=account_raw.get("equity", 0.0),
        )
        daily_stats = guardrails.DailyStats(
            trades_today=decision_db.count_trades_today(db_path),
            drawdown_pct_today=decision_db.compute_daily_drawdown_pct(
                db_path, starting_balance=account.balance or 1.0
            ),
        )
        trade_decision = guardrails.TradeDecision(
            symbol=symbol,
            direction=parsed.get("direction", "none"),
            size=parsed.get("size") or 0.0,
            stop_loss=parsed.get("stop_loss"),
            take_profit=parsed.get("take_profit"),
        )
        outcome = guardrails.evaluate_guardrails(
            trade_decision, account, daily_stats, guardrail_config=env["guardrail_config"]
        )

        executed = False
        if outcome.result == guardrails.GuardrailResult.APPROVED and trade_decision.direction != "none":
            if dry_run:
                print(
                    f"[agent] DRY-RUN: would execute {trade_decision} "
                    f"(guardrail-approved, NOT sent to executor).",
                )
            else:
                await executor.execute_order(
                    ctrader, trade_decision, account, daily_stats, env["guardrail_config"]
                )
                executed = True
        elif outcome.result == guardrails.GuardrailResult.REJECTED:
            print(f"[agent] Guardrail REJECTED: {outcome.reason}", file=sys.stderr)

        decision_db.insert_decision(
            db_path,
            m15_candle_close_time=ctx.m15_candle_close_time,
            symbol=symbol,
            d1_bias=ctx.d1_bias,
            h4_setup_valid=ctx.h4_setup_valid,
            h4_setup_type=ctx.h4_setup_type,
            m15_trigger_type=ctx.m15_trigger_type,
            market_context=ctx.to_json_dict(),
            llm_raw_response=raw,
            decision=trade_decision.direction,
            size=trade_decision.size,
            stop_loss=trade_decision.stop_loss,
            take_profit=trade_decision.take_profit,
            guardrail_status=outcome.result.value,
            guardrail_reason=outcome.reason,
            executed=executed,
            processing_latency_ms=int((time.monotonic() - cycle_start) * 1000),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="TradAlgo trading decision agent")
    parser.add_argument("--dry-run", action="store_true", help="Log decisions without executing orders.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one cycle and exit (continuous looping is scheduler/candle_scheduler.py's job).",
    )
    args = parser.parse_args()
    if not args.once:
        print(
            "[agent] Continuous looping is not implemented here -- run a single cycle per "
            "invocation and use scheduler/candle_scheduler.py for continuous, M15-aligned runs.",
            file=sys.stderr,
        )
    asyncio.run(run_cycle(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
