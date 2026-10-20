"""One turn: the user's message in, the endpoint's answer or question out."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from telegram_bot_gateway.application.model import PENDING_TTL_SECONDS, Pending, Session
from telegram_bot_gateway.application.ports import Llm, ReasoningSink
from telegram_bot_gateway.domain import clarify
from telegram_bot_gateway.domain.content import drop_images, text_of

MAX_ROUNDS = 8
UNSUPPORTED_TOOL = "This client cannot run that tool."


@dataclass
class Answer:
    text: str
    notices: list[str] = field(default_factory=list)


@dataclass
class Ask:
    question: clarify.Question
    tool_call_id: str
    notices: list[str] = field(default_factory=list)


Outcome = Answer | Ask


def begin(session: Session, content: str | list[dict[str, Any]], *, now: float) -> tuple[Session, Pending | None]:
    """A copy of the session with the user's message added, and any question that went stale.

    A message sent while a question is open answers it; a question older than a day is dropped
    together with the assistant turn that asked it.
    """
    work = copy.deepcopy(session)
    stale: Pending | None = None
    pending = work.pending
    if pending is not None and now - pending.created_at > PENDING_TTL_SECONDS:
        stale = pending
        work.pending = None
        while work.messages and work.messages[-1].get("role") == "tool":
            work.messages.pop()
        if work.messages and work.messages[-1].get("role") == "assistant" and work.messages[-1].get("tool_calls"):
            work.messages.pop()
        pending = None
    if pending is not None:
        work.messages.append({"role": "tool", "tool_call_id": pending.tool_call_id, "content": text_of(content) or "(no answer)"})
        work.pending = None
    else:
        work.messages = drop_images(work.messages)
        work.messages.append({"role": "user", "content": content})
    return work, stale


async def run(
    work: Session,
    llm: Llm,
    api_key: str,
    *,
    default_model: str,
    chat_id: int,
    now: float,
    on_reasoning: ReasoningSink | None = None,
    system: str | None = None,
) -> Outcome:
    """Call the endpoint until it answers or asks. ``work`` is changed; the caller saves it on success.

    ``system`` is sent first on every call and never stored, so it can change without touching history.
    """
    params = dict(work.params)
    notices: list[str] = []
    for _ in range(MAX_ROUNDS):
        sent = [{"role": "system", "content": system}, *work.messages] if system else work.messages
        message = await llm.chat(api_key, work.model or default_model, sent, params, [clarify.TOOL], on_reasoning)
        work.messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            return Answer(text_of(message.get("content")), notices)
        asked: tuple[dict[str, Any], clarify.Question] | None = None
        for call in calls:
            name = call.get("function", {}).get("name")
            if name == clarify.NAME and asked is None:
                asked = (call, clarify.parse(call["function"].get("arguments", "")))
            else:
                notices.append(f"The endpoint asked for an unsupported tool: {name}")
                work.messages.append({"role": "tool", "tool_call_id": call["id"], "content": UNSUPPORTED_TOOL})
        if asked is not None:
            call, question = asked
            work.pending = Pending(call["id"], question.text, list(question.options), chat_id, now)
            return Ask(question, call["id"], notices)
    notices.append("Stopped: the endpoint kept calling tools.")
    return Answer("", notices)
