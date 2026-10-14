"""Turning catalog hits into context a model can use."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

UNAVAILABLE = (
    "The knowledge catalog could not be consulted just now. Say so if it matters to the answer, "
    "and do not guess what it would have contained."
)


def format_hits(hits: Sequence[dict[str, Any]]) -> str:
    if not hits:
        return "Knowledge catalog: no matching entries. Say so rather than assuming."
    lines = ["Knowledge catalog results (read the real document via its fetch hint before relying on a summary):"]
    for hit in hits:
        title = _first(hit.get("title"))
        summary = _first(hit.get("summary"))
        lines.append(f"- {title} [{hit.get('type', 'doc')}]: {summary}")
        fetch = hit.get("fetch")
        if fetch:
            lines.append(f"  fetch: {fetch.get('tool')} {fetch.get('args')}")
        if hit.get("stale"):
            lines.append("  (summary may be stale)")
    return "\n".join(lines)


def _first(value: Any) -> str:
    if isinstance(value, dict):
        for text in value.values():
            if text:
                return str(text)
        return ""
    return str(value or "")
