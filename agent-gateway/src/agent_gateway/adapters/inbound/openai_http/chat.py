"""``POST /v1/chat/completions``: agents run the loop; every other model goes straight upstream."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from agent_gateway.adapters.inbound.openai_http import wire
from agent_gateway.adapters.inbound.openai_http.auth import Caller, authenticate
from agent_gateway.adapters.inbound.openai_http.principal import principal_of
from agent_gateway.adapters.inbound.openai_http.proxy import forward
from agent_gateway.application.events import ContentDelta, Done, Event, ReasoningDelta, ToolCallsOut
from agent_gateway.application.models import TurnRequest
from agent_gateway.application.use_cases.run_turn import run_turn
from agent_gateway.domain.errors import GatewayError, InvalidRequest
from agent_gateway.domain.identity import Principal
from agent_gateway.domain.messages import ChatMessage, ToolCall, ToolDef, Usage
from agent_gateway.domain.params import TUNABLE, check_supported
from agent_gateway.domain.routing import parse_route

router = APIRouter()
KEEPALIVE_SECONDS = 15.0


class QueueSink:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event | BaseException | None] = asyncio.Queue()

    async def emit(self, event: Event) -> None:
        await self.queue.put(event)


def request_vars(headers, servers) -> dict[str, dict[str, str]]:
    """Credential headers (``<prefix><VAR>``) grouped by the tool server that declares them."""
    out: dict[str, dict[str, str]] = {}
    for name, value in headers.items():
        lowered = name.lower()
        for server in servers.values():
            if server.header_prefix and lowered.startswith(server.header_prefix.lower()):
                var = lowered[len(server.header_prefix):].replace("-", "_").upper()
                if var in server.credentials:
                    out.setdefault(server.name, {})[var] = value
    return out


def build_turn(body: dict[str, Any], route, caller: Caller, principal: Principal, request: Request) -> TurnRequest:
    container = request.app.state.container
    try:
        messages = [ChatMessage.model_validate(m) for m in body.get("messages") or []]
        tools = [ToolDef.model_validate(t) for t in body.get("tools") or [] if t.get("type", "function") == "function"]
    except ValidationError as exc:
        raise InvalidRequest(f"Invalid messages or tools: {exc.errors()[0]['msg']}", param="messages") from exc
    if not messages:
        raise InvalidRequest("'messages' must contain at least one message.", param="messages")
    return TurnRequest(
        principal=caller, agent_id=route.agent_id, upstream_model=route.upstream_model, messages=messages,
        client_tools=tools, params={k: body[k] for k in TUNABLE if k in body}, stream=bool(body.get("stream")),
        request_vars=request_vars(request.headers, container.config.servers))


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, caller: Caller = Depends(authenticate)):
    container = request.app.state.container
    raw = await request.body()
    if len(raw) > container.config.limits.max_body_bytes:
        return JSONResponse(wire_error("Request body too large.", "invalid_request_error"), status_code=413)
    try:
        body = json.loads(raw)
        model = body["model"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidRequest("Request body must be JSON with a 'model'.", param="model") from exc
    principal = principal_of(request, caller)
    route = parse_route(str(model), container.config.agents, principal)
    if route.kind == "passthrough":
        return await forward(request, "chat/completions", caller, raw)
    check_supported(body)
    turn = build_turn(body, route, caller, principal, request)
    deps_for = lambda sink: dataclasses.replace(container.deps, sink=sink)  # noqa: E731
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    if turn.stream:
        return await _stream(container, turn, deps_for, str(model), include_usage)
    sink = CollectingSink()
    await run_turn(turn, deps_for(sink), container.lock, container.driver)
    return JSONResponse(wire.completion(
        id=wire.new_id(), model=str(model), content=sink.content, reasoning=sink.reasoning,
        calls=sink.calls, finish=sink.finish, usage=sink.usage))


def wire_error(message: str, type_: str) -> dict:
    return {"error": {"message": message, "type": type_, "param": None, "code": None}}


class CollectingSink:
    def __init__(self) -> None:
        self.content, self.reasoning, self.calls = "", "", []
        self.finish, self.usage = "stop", Usage()

    async def emit(self, event: Event) -> None:
        if isinstance(event, ContentDelta):
            self.content += event.text
        elif isinstance(event, ReasoningDelta):
            self.reasoning += event.text
        elif isinstance(event, ToolCallsOut):
            self.calls = list(event.calls)
        elif isinstance(event, Done):
            self.finish, self.usage = event.finish_reason, event.usage


async def _stream(container, turn: TurnRequest, deps_for, model: str, include_usage: bool):
    sink = QueueSink()

    async def runner() -> None:
        try:
            await run_turn(turn, deps_for(sink), container.lock, container.driver)
            await sink.queue.put(None)
        except BaseException as exc:  # noqa: BLE001 -- relayed to the stream consumer
            await sink.queue.put(exc)

    task = asyncio.create_task(runner())
    first = await sink.queue.get()
    if isinstance(first, BaseException):                      # failed before any output: a normal HTTP error
        raise first
    ident, created = wire.new_id(), int(time.time())

    async def body() -> AsyncIterator[str]:
        backlog: list[Event | BaseException | None] = [first]
        yield wire.chunk(id=ident, model=model, created=created, delta={"role": "assistant", "content": ""})
        try:
            while True:
                if backlog:
                    item = backlog.pop()
                else:
                    try:
                        item = await asyncio.wait_for(sink.queue.get(), KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield wire.KEEPALIVE
                        continue
                if item is None:
                    break
                if isinstance(item, BaseException):
                    err = item if isinstance(item, GatewayError) else GatewayError("The server had an error while processing your request.")
                    yield "data: " + json.dumps(wire_error(err.message, err.type)) + "\n\n"
                    break
                if isinstance(item, ContentDelta):
                    yield wire.chunk(id=ident, model=model, created=created, delta={"content": item.text})
                elif isinstance(item, ReasoningDelta):
                    yield wire.chunk(id=ident, model=model, created=created, delta={"reasoning_content": item.text})
                elif isinstance(item, ToolCallsOut):
                    calls = wire.tool_calls_json(item.calls)
                    yield wire.chunk(id=ident, model=model, created=created,
                                     delta={"tool_calls": [{"index": i, **c} for i, c in enumerate(calls)]})
                elif isinstance(item, Done):
                    yield wire.chunk(id=ident, model=model, created=created, delta={}, finish=item.finish_reason)
                    if include_usage:
                        yield wire.chunk(id=ident, model=model, created=created, delta={}, usage=item.usage, empty_choices=True)
            yield wire.DONE
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
