"""Short, secret-free messages that tell a model what it got wrong and how to fix it."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def unknown_tool(name: str, valid: Iterable[str]) -> str:
    names = ", ".join(sorted(valid)) or "(none)"
    return f"Error: there is no tool named '{name}'. Call one of: {names}."


def invalid_arguments_json(name: str, detail: str) -> str:
    return f"Error: the arguments for '{name}' are not valid JSON ({detail}). Send one JSON object."


def schema_violation(name: str, problems: Iterable[str]) -> str:
    listed = "; ".join(list(problems)[:5])
    return f"Error: the arguments for '{name}' do not match its schema: {listed}. Fix them and call it again."


def empty_reply() -> str:
    return "Error: your reply was empty. Answer the user, or call a tool."


def tool_failed(name: str, message: str) -> str:
    return f"The tool '{name}' failed: {message[:400]}. Adjust the call or tell the user."


def declined(name: str) -> str:
    return f"The user declined '{name}'. Do not retry it; acknowledge and continue."


def parse_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a tool-call arguments string; returns (object, error detail)."""
    if not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{exc.msg} at position {exc.pos}"
    if not isinstance(value, dict):
        return None, "expected a JSON object"
    return value, None


def schema_problems(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """A small structural check: required keys and primitive types of top-level properties."""
    problems: list[str] = []
    props = schema.get("properties", {}) or {}
    for key in schema.get("required", []) or []:
        if key not in args:
            problems.append(f"missing required '{key}'")
    kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "array": list, "object": dict}
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            continue
        kind = spec.get("type")
        expected = kinds.get(kind) if isinstance(kind, str) else None
        if expected and (not isinstance(value, expected) or (kind in {"integer", "number"} and isinstance(value, bool))):
            problems.append(f"'{key}' must be {kind}")
    return problems


def redact(text: str, secrets: Iterable[str]) -> str:
    """Remove credential values from text before it reaches a model or a log."""
    for secret in secrets:
        if len(secret) >= 4:
            text = text.replace(secret, "***")
    return text
