"""Who runs a tool call: the user's client, or the gateway."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

_MCP_PREFIX = re.compile(r"^mcp__.+?__")
_MCP_SUFFIX = re.compile(r"_mcp_[a-z0-9-]+$")


def base_name(name: str, servers: Iterable[str] = ()) -> str:
    """A tool's name without client or server decoration, lower-cased, ``-`` as ``_``.

    ``mcp__atlassian__jira_create_issue``, ``jira_create_issue_mcp_x`` and
    ``jira_create_issue`` all normalize to ``jira_create_issue``.
    """
    n = name.strip().lower()
    n = _MCP_PREFIX.sub("", n)
    n = _MCP_SUFFIX.sub("", n)
    for server in servers:
        prefix = server.lower().replace("-", "_") + "_"
        flat = n.replace("-", "_")
        if flat.startswith(prefix) and len(flat) > len(prefix):
            n = flat[len(prefix):]
            break
    return n.replace("-", "_")


def matching_client_tools(
    tool: str,
    client_tools: Iterable[str],
    *,
    servers: Iterable[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> list[str]:
    """Every client tool that is the same tool as ``tool`` (by normalized base name or alias)."""
    servers = tuple(servers)
    aliases = aliases or {}
    wanted = {base_name(tool, servers), base_name(aliases.get(tool, tool), servers)}
    return [c for c in client_tools if c == aliases.get(tool) or base_name(c, servers) in wanted]


def mentions_any(client_tool: str, terms: Iterable[str]) -> bool:
    """True when a client tool's name contains one of the words, e.g. ``connectors__jira__SEARCH_ISSUES`` for ``jira``."""
    words = {w for w in re.split(r"[^a-z0-9]+", client_tool.lower()) if w}
    return any(t.lower() in words for t in terms)


def match_client_tool(
    tool: str,
    client_tools: Iterable[str],
    *,
    servers: Iterable[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> str | None:
    """The client's own name for ``tool``, matched by normalized base name or alias only."""
    servers = tuple(servers)
    aliases = aliases or {}
    wanted = {base_name(tool, servers), base_name(aliases.get(tool, tool), servers)}
    for candidate in client_tools:
        if candidate == aliases.get(tool) or base_name(candidate, servers) in wanted:
            return candidate
    return None


@dataclass(frozen=True)
class ToolRoute:
    kind: Literal["client", "gateway", "unavailable"]
    client_name: str | None = None
    credentials: Literal["none", "request", "stored"] | None = None


def choose_route(
    *,
    tool: str,
    client_name: str | None,
    needs_credentials: bool,
    request_vars: Mapping[str, str],
    stored_vars: Mapping[str, str],
) -> ToolRoute:
    """The agent's own tool first: the gateway with request credentials, then stored ones.

    Only when the gateway cannot run it does the client's equivalent tool get the call; else nobody can.
    """
    if not needs_credentials:
        return ToolRoute("gateway", credentials="none")
    if request_vars:
        return ToolRoute("gateway", credentials="request")
    if stored_vars:
        return ToolRoute("gateway", credentials="stored")
    if client_name is not None:
        return ToolRoute("client", client_name=client_name)
    return ToolRoute("unavailable")


def resolve_vars(
    request_vars: Mapping[str, str], stored_vars: Mapping[str, str], declared: Iterable[str]
) -> dict[str, str]:
    """Per variable, a request value wins over a stored one; undeclared variables are dropped."""
    out: dict[str, str] = {}
    for var in declared:
        if var in request_vars:
            out[var] = request_vars[var]
        elif var in stored_vars:
            out[var] = stored_vars[var]
    return out
