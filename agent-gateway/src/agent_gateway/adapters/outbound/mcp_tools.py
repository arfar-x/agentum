"""Tool servers over MCP (streamable HTTP): listing, calling and the catalog lookup."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from agent_gateway.application.models import ServerInfo, ToolResult, ToolSpec

ClientFactory = Callable[[str, float, dict[str, str]], Client]


def default_client_factory(url: str, timeout: float, headers: dict[str, str]) -> Client:
    return Client(StreamableHttpTransport(url, headers=headers), timeout=timeout)


def _text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    if not parts and getattr(result, "structured_content", None) is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    return "\n".join(parts)


class McpTools:
    """``ToolGateway`` over one or more MCP servers.

    Tool lists need no credentials, so they are cached per server. A call opens its own short-lived
    connection carrying that call's credential headers, so nothing credentialed is shared between users.
    """

    def __init__(self, urls: Mapping[str, tuple[str, float]], servers: Mapping[str, ServerInfo],
                 factory: ClientFactory = default_client_factory, list_ttl: float = 300.0) -> None:
        self._urls, self._servers, self._factory, self._ttl = urls, servers, factory, list_ttl
        self._cache: dict[str, tuple[float, list[ToolSpec]]] = {}

    async def list_tools(self, server: str) -> list[ToolSpec]:
        cached = self._cache.get(server)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        url, timeout = self._urls[server]
        try:
            async with self._factory(url, timeout, {}) as client:
                tools = await client.list_tools()
        except Exception:  # noqa: BLE001 -- a down server hides its tools; it must not break the turn
            return cached[1] if cached else []
        specs = [ToolSpec(server, t.name, t.description or "", dict(t.inputSchema or {"type": "object", "properties": {}}))
                 for t in tools]
        self._cache[server] = (time.monotonic(), specs)
        return specs

    def _headers(self, server: str, credentials: Mapping[str, str]) -> dict[str, str]:
        prefix = self._servers[server].header_prefix
        declared = set(self._servers[server].credentials)
        return {f"{prefix}{k}": v for k, v in credentials.items() if prefix and k in declared}

    async def call(self, server: str, tool: str, arguments: dict[str, Any], credentials: dict[str, str]) -> ToolResult:
        url, timeout = self._urls[server]
        try:
            async with self._factory(url, timeout, self._headers(server, credentials)) as client:
                result = await client.call_tool(tool, arguments, raise_on_error=False)
        except Exception as exc:  # noqa: BLE001 -- failures go back to the model as tool errors
            return ToolResult(f"{type(exc).__name__}: {str(exc)[:300]}", is_error=True)
        return ToolResult(_text(result), is_error=bool(getattr(result, "is_error", False)))


class KbRetriever:
    """The catalog lookup, over the same MCP plumbing. Never sends credentials."""

    def __init__(self, tools: McpTools, server: str, tool: str) -> None:
        self._tools, self._server, self._tool = tools, server, tool

    async def search(self, queries: list[str], limit: int, types: tuple[str, ...]) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"queries": queries, "limit": limit}
        if types:
            args["types"] = list(types)
        result = await self._tools.call(self._server, self._tool, args, {})
        if result.is_error:
            raise RuntimeError(result.text)
        data = json.loads(result.text)
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return list(data.get("hits", []))
