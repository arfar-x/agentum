"""Per-session generation settings: which exist, and what values they take."""

from __future__ import annotations

import json
from typing import Any

REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")
VERBOSITIES = ("low", "medium", "high")

#: name -> (kind, low, high)
PARAMS: dict[str, tuple[str, float | None, float | None]] = {
    "temperature": ("float", 0, 2),
    "top_p": ("float", 0, 1),
    "max_tokens": ("int", 1, None),
    "seed": ("int", None, None),
    "verbosity": ("choice", None, None),
    "presence_penalty": ("float", -2, 2),
    "frequency_penalty": ("float", -2, 2),
    "stop": ("stop", None, None),
}


def parse(name: str, raw: str) -> Any:
    """The typed value for ``name`` from the user's text, or a ValueError saying what is wrong."""
    if name not in PARAMS:
        raise ValueError(f"unknown setting '{name}'; choose from {', '.join(PARAMS)}")
    kind, low, high = PARAMS[name]
    raw = raw.strip()
    if not raw:
        raise ValueError(f"give a value for {name}")
    if kind == "choice":
        if raw.lower() not in VERBOSITIES:
            raise ValueError(f"{name} must be one of {', '.join(VERBOSITIES)}")
        return raw.lower()
    if kind == "stop":
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except ValueError as exc:
                raise ValueError("stop must be text or a JSON list of text") from exc
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or not 1 <= len(value) <= 4:
                raise ValueError("stop takes one to four strings")
            return value
        return raw
    try:
        number: int | float = int(raw) if kind == "int" else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be {'a whole number' if kind == 'int' else 'a number'}") from exc
    if (low is not None and number < low) or (high is not None and number > high):
        bounds = f"between {low} and {high}" if low is not None and high is not None else f"at least {low}"
        raise ValueError(f"{name} must be {bounds}")
    return number


def parse_reasoning(raw: str) -> str:
    if raw.lower() not in REASONING_EFFORTS:
        raise ValueError(f"reasoning must be one of {', '.join(REASONING_EFFORTS)}")
    return raw.lower()
