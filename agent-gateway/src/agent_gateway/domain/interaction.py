"""Asking the user a question through whatever the client already offers.

A client that declares one of these tools gets the prompt in its own UI; any other client gets
the text fallback (see ``approval``). The detection is the request's own tool list, not a header.
"""

from __future__ import annotations

import json

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

APPROVE, REJECT = "Approve", "Reject"


@dataclass(frozen=True)
class AskTool:
    name: str
    build: Callable[[str, str, Sequence[str]], dict[str, Any]]  # (header, question, options) -> arguments


def _questions_options(header: str, question: str, options: Sequence[str]) -> dict[str, Any]:
    q: dict[str, Any] = {"question": question, "header": header[:12], "multiSelect": False}
    if options:
        q["options"] = [{"label": o, "description": ""} for o in options]
    return {"questions": [q]}


def _options_values(header: str, question: str, options: Sequence[str]) -> dict[str, Any]:
    q: dict[str, Any] = {"id": "answer", "header": header, "question": question}
    if options:
        q["options"] = [{"label": o, "value": o.lower()} for o in options]
    return {"questions": [q]}


def _questions_choices(header: str, question: str, options: Sequence[str]) -> dict[str, Any]:
    q: dict[str, Any] = {"question": question}
    if options:
        q["choices"] = list(options)
    return {"questions": [q]}


#: How a client's question tool wants its arguments. A new client is supported by naming its tool
#: and one of these styles in ``config/gateway.yaml`` (``ask_tools``), with no code change.
STYLES: dict[str, Callable[[str, str, Sequence[str]], dict[str, Any]]] = {
    "questions_options": _questions_options,    # questions=[{question, header, options:[{label, description}]}]
    "options_values": _options_values,          # questions=[{id, header, question, options:[{label, value}]}]
    "questions_choices": _questions_choices,    # questions=[{question, choices:[...]}]
}

#: Tool name -> style, for the question tools clients are known to declare.
DEFAULT_STYLES: dict[str, str] = {
    "AskUserQuestion": "questions_options",
    "question": "questions_options",
    "ask_user_question": "options_values",
    "clarify": "questions_choices",
}


def ask_registry(extra: Mapping[str, str] | None = None) -> dict[str, AskTool]:
    """The question tools the gateway can use: the defaults plus any the operator names."""
    styles = {**DEFAULT_STYLES, **(extra or {})}
    unknown = sorted(set(styles.values()) - set(STYLES))
    if unknown:
        raise ValueError(f"unknown ask-tool style(s): {', '.join(unknown)}")
    return {name: AskTool(name, STYLES[style]) for name, style in styles.items()}


KNOWN: dict[str, AskTool] = ask_registry()


def find_ask_tool(client_tools: Iterable[str], known: dict[str, AskTool] | None = None) -> AskTool | None:
    from agent_gateway.domain.toolroute import base_name

    registry = known or KNOWN
    declared = {base_name(n): n for n in client_tools}
    for key, tool in registry.items():
        hit = declared.get(base_name(key))
        if hit is not None:
            return AskTool(hit, tool.build)
    return None


_ANSWER_KEYS = ("user_response", "answer", "answers", "response", "selected", "selection", "choice", "value", "label")


def _answers(value: Any) -> list[str]:
    """The user's own answer from a structured tool result, ignoring echoed questions and offered choices."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ANSWER_KEYS:
                found += [str(x) for x in (item if isinstance(item, list) else [item]) if not isinstance(x, (dict, list))]
            elif isinstance(item, (dict, list)):
                found += _answers(item)
    elif isinstance(value, list):
        for item in value:
            found += _answers(item)
    return found


def parse_answer(result_text: str) -> bool | None:
    """True for an approval, False for a rejection, None when the answer is unclear."""
    try:
        parsed = json.loads(result_text)
    except ValueError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        answers = _answers(parsed)
        if not answers:
            return None
        result_text = " ".join(answers)
    text = result_text.lower()
    rejected = any(w in text for w in ("reject", "deny", "denied", "decline", "no,"))
    approved = "approve" in text or "yes" in text.split()
    if rejected and not approved:
        return False
    if approved and not rejected:
        return True
    return None
