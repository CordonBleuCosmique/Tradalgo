"""
broker/ctrader_client.py

Remote MCP client for cTrader (per help.ctrader.com/ctrader-ai-agent-connect).

Uses Remote MCP -- a bearer-token-authenticated, streamable-HTTP/SSE MCP
server -- NOT Local MCP, which requires the cTrader desktop GUI app to be
running and is therefore incompatible with a headless Raspberry Pi /
mini-PC daemon.

The bearer token is generated from cTrader Web -> Settings -> Remote MCP
and is tied to an active cTrader Web session: it can expire and must be
regenerated manually. This client never retries auth failures silently in
a loop -- `check_token_health()` is a single-attempt probe, and callers
(core/agent.py) are responsible for surfacing a clear one-time log message
and waiting for human intervention.

Only the 6 tool names documented by cTrader's Remote MCP are called, and
they are used exactly as named -- no invented tool names:
get_balance, get_positions, get_deals, get_trendbars, create_order,
cancel_order.
"""
import json
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class MCPToolError(Exception):
    """Raised when an MCP tool call returns an error result or the
    connection/auth fails."""


class CTraderClient:
    """
    Thin async wrapper around an MCP ClientSession connected to cTrader's
    Remote MCP server.

    `session_factory` is the testability seam: it must be a zero-argument
    callable returning an async context manager that yields an object with
    an async `call_tool(name, arguments)` method (i.e. anything shaped like
    `mcp.ClientSession`). If omitted, a real streamable-HTTP session against
    `server_url` with the bearer token in the `Authorization` header is
    used. Tests can pass a fake factory yielding a fake session with no
    network I/O at all.
    """

    def __init__(
        self,
        *,
        server_url: str,
        bearer_token: str,
        session_factory: Optional[Callable[[], Any]] = None,
    ):
        self.server_url = server_url
        self.bearer_token = bearer_token
        self._session_factory = session_factory or self._default_session_factory

    @asynccontextmanager
    async def _default_session_factory(self):
        async with streamablehttp_client(
            self.server_url,
            headers={"Authorization": f"Bearer {self.bearer_token}"},
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def _call_tool(self, name: str, arguments: dict) -> Any:
        """
        Opens a session via `session_factory`, calls `session.call_tool`,
        and returns the parsed result. Raises `MCPToolError` if the tool
        call itself reports an error (e.g. an invalid/expired token) --
        callers decide whether to retry; this method never retries.
        """
        async with self._session_factory() as session:
            result = await session.call_tool(name, arguments)

        if getattr(result, "isError", False):
            raise MCPToolError(f"MCP tool '{name}' returned an error: {result.content!r}")

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured

        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return None

    async def get_balance(self) -> dict:
        return await self._call_tool("get_balance", {})

    async def get_positions(self) -> list[dict]:
        result = await self._call_tool("get_positions", {})
        return result if isinstance(result, list) else result.get("positions", []) if result else []

    async def get_deals(self, *, count: int = 50) -> list[dict]:
        result = await self._call_tool("get_deals", {"count": count})
        return result if isinstance(result, list) else result.get("deals", []) if result else []

    async def get_trendbars(self, symbol: str, timeframe: str, count: int) -> list[dict]:
        result = await self._call_tool(
            "get_trendbars", {"symbol": symbol, "timeframe": timeframe, "count": count}
        )
        return result if isinstance(result, list) else result.get("trendbars", []) if result else []

    async def create_order(
        self,
        *,
        symbol: str,
        direction: str,
        size: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
    ) -> dict:
        return await self._call_tool(
            "create_order",
            {
                "symbol": symbol,
                "direction": direction,
                "size": size,
                "stopLoss": stop_loss,
                "takeProfit": take_profit,
            },
        )

    async def cancel_order(self, order_id: str) -> dict:
        return await self._call_tool("cancel_order", {"orderId": order_id})

    async def check_token_health(self) -> bool:
        """
        Lightweight, non-destructive health probe: calls `get_balance()`
        and returns True/False. Never raises -- this is a boolean gate
        called by `core/agent.py` every cycle, before any multi-TF
        analysis. No retry loop inside this method; one attempt, one
        boolean result, logged once by the caller.
        """
        try:
            await self.get_balance()
            return True
        except Exception:
            return False
