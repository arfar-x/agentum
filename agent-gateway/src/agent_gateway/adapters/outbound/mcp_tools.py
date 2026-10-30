"""Tool servers over MCP (streamable HTTP): listing, calling and the catalog lookup."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from agent_gateway.application.models import ServerInfo, ToolResult, ToolSpec
from agent_gateway.domain.identity import MAX_NAME_LENGTH, Principal

log = logging.getLogger(__name__)

GROUPS_HEADER = "X-KB-Groups"
TRUST_HEADER = "X-KB-Trust"
MAX_FORWARDED_GROUPS = 64
MAX_FORWARDED_BYTES = 4096

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


def forwarded_groups(groups: Iterable[str]) -> str:
    """The ``X-KB-Groups`` value: valid names only (no comma, no control character, 1 to 128 chars),
    unique, at most 64 names and 4 KiB. Anything over a limit is cut, with a log line."""
    names: list[str] = []
    size = 0
    cut = 0
    for name in dict.fromkeys(groups):
        if not name or len(name) > MAX_NAME_LENGTH or "," in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
            continue
        added = len(name.encode()) + (1 if names else 0)
        if len(names) >= MAX_FORWARDED_GROUPS or size + added > MAX_FORWARDED_BYTES:
            cut += 1
            continue
        names.append(name)
        size += added
    if cut:
        log.warning("group list too long for the catalog header: %d name(s) left out", cut)
    return ",".join(names)


class McpTools:
    """``ToolGateway`` over one or more MCP servers.

    Tool lists need no credentials, so they are cached per server. A call opens its own short-lived
    connection carrying that call's credential headers, so nothing credentialed is shared between users.
    """

    def __init__(self, urls: Mapping[str, tuple[str, float]], servers: Mapping[str, ServerInfo],
                 factory: ClientFactory = default_client_factory, list_ttl: float = 300.0,
                 trust_secret: str = "") -> None:
        self._urls, self._servers, self._factory, self._ttl = urls, servers, factory, list_ttl
        self._trust_secret = trust_secret
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

    def _headers(self, server: str, credentials: Mapping[str, str],
                 identity: Principal | None = None) -> dict[str, str]:
        info = self._servers[server]
        prefix = info.header_prefix
        declared = set(info.credentials)
        headers = {f"{prefix}{k}": v for k, v in credentials.items() if prefix and k in declared}
        if info.forward_identity and self._trust_secret and identity is not None:
            groups = forwarded_groups(identity.groups)
            if groups:
                headers[GROUPS_HEADER] = groups
                headers[TRUST_HEADER] = self._trust_secret
        return headers

    async def call(self, server: str, tool: str, arguments: dict[str, Any], credentials: dict[str, str],
                   *, identity: Principal | None = None) -> ToolResult:
        url, timeout = self._urls[server]
        try:
            async with self._factory(url, timeout, self._headers(server, credentials, identity)) as client:
                result = await client.call_tool(tool, arguments, raise_on_error=False)
        except Exception as exc:  # noqa: BLE001 -- failures go back to the model as tool errors
            return ToolResult(f"{type(exc).__name__}: {str(exc)[:300]}", is_error=True)
        return ToolResult(_text(result), is_error=bool(getattr(result, "is_error", False)))


class KbRetriever:
    """The catalog lookup, over the same MCP plumbing. Never sends credentials; sends the caller's
    groups when the server is configured to receive them."""

    def __init__(self, tools: McpTools, server: str, tool: str) -> None:
        self._tools, self._server, self._tool = tools, server, tool

    async def search(self, queries: list[str], limit: int, types: tuple[str, ...], *,
                     identity: Principal | None = None) -> list[dict[str, Any]]:
        # The catalog requires the question rewritten in its own language
        # (`catalog_queries`). This lookup runs before any model sees the turn,
        # so there is nothing to translate with: the user's words go in both
        # places, which is what the catalog accepted before the field existed.
        args: dict[str, Any] = {"queries": queries, "catalog_queries": queries, "limit": limit}
        if types:
            args["types"] = list(types)
        result = await self._tools.call(self._server, self._tool, args, {}, identity=identity)
        if result.is_error:
            raise RuntimeError(result.text)
        data = json.loads(result.text)
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return list(data.get("hits", []))
