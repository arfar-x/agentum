"""OpenAI Responses API shapes: input conversion and the output/event builder."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from agent_gateway.application.events import ContentDelta, Done, Event, ReasoningDelta, ToolCallsOut
from agent_gateway.domain.errors import InvalidRequest
from agent_gateway.domain.messages import ChatMessage, FunctionCall, ToolCall, ToolDef, ToolFunction, Usage


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") in {"input_text", "output_text", "text"}:
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def input_to_messages(body: dict[str, Any], previous: list[ChatMessage] | None) -> list[ChatMessage]:
    messages: list[ChatMessage] = list(previous or [])
    if body.get("instructions"):
        messages.insert(0, ChatMessage(role="system", content=str(body["instructions"])))
    raw = body.get("input")
    if isinstance(raw, str):
        messages.append(ChatMessage(role="user", content=raw))
        return messages
    if not isinstance(raw, list) or not raw:
        raise InvalidRequest("'input' must be a string or a non-empty list of items.", param="input")
    for item in raw:
        kind = item.get("type", "message") if isinstance(item, dict) else None
        if kind == "message":
            role = {"developer": "system"}.get(item.get("role"), item.get("role", "user"))
            messages.append(ChatMessage(role=role, content=_text(item.get("content"))))
        elif kind == "function_call":
            call = ToolCall(id=item["call_id"], function=FunctionCall(name=item["name"], arguments=item.get("arguments", "")))
            if messages and messages[-1].role == "assistant" and messages[-1].tool_calls is not None and not messages[-1].text():
                messages[-1].tool_calls.append(call)
            else:
                messages.append(ChatMessage(role="assistant", content=None, tool_calls=[call]))
        elif kind == "function_call_output":
            out = item.get("output")
            messages.append(ChatMessage(role="tool", tool_call_id=item["call_id"],
                                        content=out if isinstance(out, str) else json.dumps(out)))
        elif kind in {"reasoning", "item_reference"}:
            continue
        else:
            raise InvalidRequest(f"Unsupported input item type {kind!r}.", param="input")
    return messages


def tools_from(body: dict[str, Any]) -> list[ToolDef]:
    out = []
    for t in body.get("tools") or []:
        if t.get("type") == "function":
            out.append(ToolDef(function=ToolFunction(name=t["name"], description=t.get("description") or "",
                                                     parameters=t.get("parameters") or {"type": "object", "properties": {}})))
    return out


class ResponseBuilder:
    """Turns turn events into output items and (optionally) the SSE events that announce them."""

    def __init__(self, response_id: str, model: str, extra: dict[str, Any] | None = None) -> None:
        self.id, self.model, self.created = response_id, model, int(time.time())
        self.extra = extra or {}
        self.items: list[dict[str, Any]] = []
        self.seq = 0
        self._open: tuple[str, dict[str, Any]] | None = None   # (kind, item)
        self.usage = Usage()

    # -- SSE plumbing
    def _sse(self, name: str, data: dict[str, Any]) -> str:
        data = {"type": name, **data, "sequence_number": self.seq}
        self.seq += 1
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def response(self, status: str = "completed") -> dict[str, Any]:
        return {
            "id": self.id, "object": "response", "created_at": self.created, "status": status, "model": self.model,
            "output": [dict(i) for i in self.items], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "error": None, "incomplete_details": None, "metadata": {}, "store": True, "text": {"format": {"type": "text"}},
            "usage": {"input_tokens": self.usage.prompt_tokens, "output_tokens": self.usage.completion_tokens,
                      "total_tokens": self.usage.total_tokens,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
            **self.extra,
        }

    def start(self) -> list[str]:
        shell = {**self.response("in_progress"), "output": [], "usage": None}
        return [self._sse("response.created", {"response": shell}), self._sse("response.in_progress", {"response": shell})]

    # -- items
    def _begin(self, kind: str) -> list[str]:
        out = self._close()
        index = len(self.items)
        if kind == "message":
            item = {"id": new_id("msg"), "type": "message", "status": "in_progress", "role": "assistant", "content": []}
            self.items.append(item)
            out.append(self._sse("response.output_item.added", {"output_index": index, "item": dict(item, content=[])}))
            part = {"type": "output_text", "text": "", "annotations": []}
            item["content"].append(part)
            out.append(self._sse("response.content_part.added", {"item_id": item["id"], "output_index": index,
                                                                  "content_index": 0, "part": dict(part)}))
        else:
            item = {"id": new_id("rs"), "type": "reasoning", "summary": [{"type": "summary_text", "text": ""}]}
            self.items.append(item)
            out.append(self._sse("response.output_item.added", {"output_index": index, "item": dict(item, summary=[])}))
            out.append(self._sse("response.reasoning_summary_part.added", {
                "item_id": item["id"], "output_index": index, "summary_index": 0, "part": {"type": "summary_text", "text": ""}}))
        self._open = (kind, item)
        return out

    def _close(self) -> list[str]:
        if self._open is None:
            return []
        kind, item = self._open
        index = self.items.index(item)
        out: list[str] = []
        if kind == "message":
            text = item["content"][0]["text"]
            item["status"] = "completed"
            out.append(self._sse("response.output_text.done", {"item_id": item["id"], "output_index": index, "content_index": 0, "text": text}))
            out.append(self._sse("response.content_part.done", {"item_id": item["id"], "output_index": index, "content_index": 0,
                                                                 "part": dict(item["content"][0])}))
        else:
            text = item["summary"][0]["text"]
            out.append(self._sse("response.reasoning_summary_text.done", {"item_id": item["id"], "output_index": index, "summary_index": 0, "text": text}))
            out.append(self._sse("response.reasoning_summary_part.done", {"item_id": item["id"], "output_index": index, "summary_index": 0,
                                                                           "part": dict(item["summary"][0])}))
        out.append(self._sse("response.output_item.done", {"output_index": index, "item": dict(item)}))
        self._open = None
        return out

    def feed(self, event: Event) -> list[str]:
        if isinstance(event, ContentDelta):
            out = [] if self._open and self._open[0] == "message" else self._begin("message")
            item = self._open[1]
            item["content"][0]["text"] += event.text
            out.append(self._sse("response.output_text.delta", {"item_id": item["id"], "output_index": self.items.index(item),
                                                                 "content_index": 0, "delta": event.text}))
            return out
        if isinstance(event, ReasoningDelta):
            out = [] if self._open and self._open[0] == "reasoning" else self._begin("reasoning")
            item = self._open[1]
            item["summary"][0]["text"] += event.text
            out.append(self._sse("response.reasoning_summary_text.delta", {
                "item_id": item["id"], "output_index": self.items.index(item), "summary_index": 0, "delta": event.text}))
            return out
        if isinstance(event, ToolCallsOut):
            out = self._close()
            for call in event.calls:
                index = len(self.items)
                item = {"id": new_id("fc"), "type": "function_call", "status": "completed", "call_id": call.id,
                        "name": call.function.name, "arguments": call.function.arguments}
                self.items.append(item)
                out.append(self._sse("response.output_item.added", {"output_index": index, "item": dict(item, arguments="", status="in_progress")}))
                out.append(self._sse("response.function_call_arguments.delta", {"item_id": item["id"], "output_index": index, "delta": call.function.arguments}))
                out.append(self._sse("response.function_call_arguments.done", {"item_id": item["id"], "output_index": index, "arguments": call.function.arguments}))
                out.append(self._sse("response.output_item.done", {"output_index": index, "item": dict(item)}))
            return out
        if isinstance(event, Done):
            self.usage = event.usage
            return self._close() + [self._sse("response.completed", {"response": self.response()})]
        return []
