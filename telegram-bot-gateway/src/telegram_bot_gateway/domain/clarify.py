"""The one tool the bot declares: a question the user answers with a button or a message."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

NAME = "clarify"

TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": "Ask the user a question and wait for the answer. Offer choices when there are a few clear ones.",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "choices": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["question"],
                    },
                }
            },
            "required": ["questions"],
        },
    },
}

APPROVAL_CHOICES = ("Approve", "Reject")


@dataclass(frozen=True)
class Question:
    text: str
    options: tuple[str, ...]

    @property
    def is_approval(self) -> bool:
        return tuple(o.lower() for o in self.options) == tuple(o.lower() for o in APPROVAL_CHOICES)


def parse(arguments: str) -> Question:
    """A question from the call's arguments, in the shape the tool declares or a flat one."""
    try:
        data = json.loads(arguments or "{}")
    except ValueError:
        return Question(arguments.strip() or "?", ())
    if isinstance(data, dict) and isinstance(data.get("questions"), list) and data["questions"]:
        data = data["questions"][0]
    if not isinstance(data, dict):
        return Question(str(data), ())
    options = data.get("choices") or data.get("options") or []
    labels = tuple(str(o.get("label", o) if isinstance(o, dict) else o) for o in options)
    return Question(str(data.get("question") or data.get("text") or "?"), labels)
