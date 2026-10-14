"""OpenAI chat-completion shapes, built from turn events."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from agent_gateway.domain.messages import ToolCall, Usage


def new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def tool_calls_json(calls: list[ToolCall]) -> list[dict[str, Any]]:
    return [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]


def usage_json(usage: Usage) -> dict[str, int]:
    return usage.model_dump()


def completion(*, id: str, model: str, content: str, reasoning: str, calls: list[ToolCall], finish: str,
               usage: Usage, created: int | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if reasoning:
        message["reasoning_content"] = reasoning
    if calls:
        message["tool_calls"] = tool_calls_json(calls)
    return {"id": id, "object": "chat.completion", "created": created or int(time.time()), "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish, "logprobs": None}],
            "usage": usage_json(usage)}


def chunk(*, id: str, model: str, created: int, delta: dict[str, Any], finish: str | None = None,
          usage: Usage | None = None, empty_choices: bool = False) -> str:
    body: dict[str, Any] = {"id": id, "object": "chat.completion.chunk", "created": created, "model": model,
                            "choices": [] if empty_choices else [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}]}
    if usage is not None:
        body["usage"] = usage_json(usage)
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


DONE = "data: [DONE]\n\n"
KEEPALIVE = ": keep-alive\n\n"
