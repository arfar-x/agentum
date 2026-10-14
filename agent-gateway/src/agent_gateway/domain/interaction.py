"""Asking the user a question through whatever the client already offers.

A client that declares one of these tools gets the prompt in its own UI; any other client gets
the text fallback (see ``approval``). The detection is the request's own tool list, not a header.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

APPROVE, REJECT = "Approve", "Reject"


@dataclass(frozen=True)
class AskTool:
    name: str
    build: Callable[[str, str], dict[str, Any]]  # (header, question) -> arguments


def _claude_like(header: str, question: str) -> dict[str, Any]:
    return {"questions": [{"question": question, "header": header[:12], "multiSelect": False,
                           "options": [{"label": APPROVE, "description": "Run it"},
                                       {"label": REJECT, "description": "Do not run it"}]}]}


def _id_options(header: str, question: str) -> dict[str, Any]:
    return {"questions": [{"id": "answer", "header": header, "question": question,
                           "options": [{"label": APPROVE, "value": "approve"},
                                       {"label": REJECT, "value": "reject"}]}]}


def _choices(header: str, question: str) -> dict[str, Any]:
    return {"question": question, "choices": [APPROVE, REJECT]}


#: Argument shapes are best-effort until verified against each client (research R5).
KNOWN: dict[str, AskTool] = {
    t.name: t
    for t in (
        AskTool("AskUserQuestion", _claude_like),
        AskTool("ask_user_question", _id_options),
        AskTool("question", _claude_like),
        AskTool("clarify", _choices),
    )
}


def find_ask_tool(client_tools: Iterable[str], known: dict[str, AskTool] | None = None) -> AskTool | None:
    from agent_gateway.domain.toolroute import base_name

    registry = known or KNOWN
    declared = {base_name(n): n for n in client_tools}
    for key, tool in registry.items():
        hit = declared.get(base_name(key))
        if hit is not None:
            return AskTool(hit, tool.build)
    return None


def parse_answer(result_text: str) -> bool | None:
    """True for an approval, False for a rejection, None when the answer is unclear."""
    text = result_text.lower()
    rejected = any(w in text for w in ("reject", "deny", "denied", "decline", "no,"))
    approved = "approve" in text or "yes" in text.split()
    if rejected and not approved:
        return False
    if approved and not rejected:
        return True
    return None
