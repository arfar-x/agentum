"""A short record of what the gateway ran for a conversation, so a later turn knows it.

A client sees only the visible replies. Without this the model, asked how it knows something,
finds an answer with no work behind it and says it guessed. The record holds the calls and their
outcome, never what the tools returned.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_ENTRIES = 40
NOTE_ENTRIES = 20
VALUE_CHARS = 200
REDACTED = "[hidden]"
_SECRET_NAMES = ("password", "token", "secret", "api_key", "apikey", "authorization", "credential")
_DROPPED = {"confirm"}  # set by the gateway on an approved call, never by the model
CATALOG_LOOKUP = "catalog lookup"


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= VALUE_CHARS else value[:VALUE_CHARS] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _clip_named(str(k), v) for k, v in value.items() if str(k) not in _DROPPED}
    if isinstance(value, Sequence):
        return [_clip(v) for v in list(value)[:10]]
    return _clip(str(value))


def _clip_named(name: str, value: Any) -> Any:
    return REDACTED if any(s in name.lower() for s in _SECRET_NAMES) else _clip(value)


def entry(agent: str, tool: str, args: Mapping[str, Any] | None, *, ok: bool, found: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"agent": agent, "tool": tool, "args": _clip(dict(args or {})), "ok": ok}
    if found is not None:
        out["found"] = found
    return out


def merge(prior: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest ``MAX_ENTRIES`` entries, oldest dropped first."""
    return [*prior, *new][-MAX_ENTRIES:]


def _line(e: Mapping[str, Any]) -> str:
    args = json.dumps(e.get("args", {}), ensure_ascii=False)
    outcome = "worked" if e.get("ok") else "failed"
    found = f", found {e['found']}" if e.get("found") is not None else ""
    return f"- {e.get('agent', '?')}: {e.get('tool', '?')} {args} -> {outcome}{found}"


def note(entries: Sequence[Mapping[str, Any]]) -> str | None:
    """What to tell the model about earlier work, or None when there is none."""
    if not entries:
        return None
    lines = "\n".join(_line(e) for e in entries[-NOTE_ENTRIES:])
    return (
        "Earlier in this conversation you ran the tools listed below on the user's behalf. The user did not "
        "see these calls, only your replies, and your earlier replies were based on this work. If asked how "
        "you know something, say what you looked up. Do not say you guessed or answered from memory when this "
        "list shows you looked it up. Only calls are listed, not their results; fetch again if you need the "
        "content.\n" + lines
    )
