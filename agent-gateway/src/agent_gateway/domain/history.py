"""Finding a conversation again from the messages a client resends."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from agent_gateway.domain.messages import ChatMessage


def _normal(message: ChatMessage) -> dict[str, object]:
    return {
        "role": message.role,
        "content": message.text().strip(),
        "calls": [(c.id, c.function.name, c.function.arguments) for c in message.tool_calls or []],
        "call_id": message.tool_call_id,
    }


def chain_hash(user_id: str, messages: Sequence[ChatMessage]) -> str:
    payload = json.dumps([user_id, [_normal(m) for m in messages]], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def lookup_hash(user_id: str, messages: Sequence[ChatMessage]) -> str | None:
    """Hash of the history up to and including the last assistant message, if there is one."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "assistant":
            return chain_hash(user_id, messages[: index + 1])
    return None
