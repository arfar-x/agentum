"""``POST /v1/responses``: agents run the loop; every other model goes straight upstream."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent_gateway.adapters.inbound.openai_http import responses_wire as rw
from agent_gateway.adapters.inbound.openai_http.auth import Caller, authenticate
from agent_gateway.adapters.inbound.openai_http.chat import KEEPALIVE_SECONDS, QueueSink, request_vars, wire_error
from agent_gateway.adapters.inbound.openai_http.proxy import forward
from agent_gateway.application.events import Event
from agent_gateway.application.models import TurnRequest
from agent_gateway.application.use_cases.run_turn import run_turn
from agent_gateway.domain.errors import GatewayError, InvalidRequest
from agent_gateway.domain.messages import ChatMessage
from agent_gateway.domain.params import check_supported
from agent_gateway.domain.routing import parse_route

router = APIRouter()


class ListSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _params(body: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {k: body[k] for k in ("temperature", "top_p", "seed", "user") if k in body}
    effort = (body.get("reasoning") or {}).get("effort")
    if effort:
        params["reasoning_effort"] = effort
    if body.get("max_output_tokens") is not None:
        params["max_completion_tokens"] = body["max_output_tokens"]
    return params


@router.post("/v1/responses")
async def responses(request: Request, caller: Caller = Depends(authenticate)):
    container = request.app.state.container
    raw = await request.body()
    if len(raw) > container.config.limits.max_body_bytes:
        return JSONResponse(wire_error("Request body too large.", "invalid_request_error"), status_code=413)
    try:
        body = json.loads(raw)
        model = str(body["model"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidRequest("Request body must be JSON with a 'model'.", param="model") from exc
    route = parse_route(model, container.config.agents)
    if route.kind == "passthrough":
        return await forward(request, "responses", raw)
    check_supported(body)
    previous = None
    if body.get("previous_response_id"):
        previous = await container.deps.store.get_response(caller.user_id, str(body["previous_response_id"]))
        if previous is None:
            raise InvalidRequest("Previous response not found.", param="previous_response_id")
    messages = rw.input_to_messages(body, previous)
    turn = TurnRequest(user_id=caller.user_id, agent_id=route.agent_id, upstream_model=route.upstream_model,
                       messages=messages, client_tools=rw.tools_from(body), params=_params(body),
                       stream=bool(body.get("stream")), request_vars=request_vars(request.headers, container.config.servers))
    response_id = rw.new_id("resp")
    extra = {"previous_response_id": body.get("previous_response_id"), "instructions": body.get("instructions")}
    builder = rw.ResponseBuilder(response_id, model, extra)

    async def remember(state) -> None:
        reply = ChatMessage(role="assistant", content=state.reply_text or None, tool_calls=state.client_calls or None)
        await container.deps.store.put_response(caller.user_id, response_id, [*messages, reply])

    def deps_for(sink):
        return dataclasses.replace(container.deps, sink=sink)

    if not turn.stream:
        sink = ListSink()
        state = await run_turn(turn, deps_for(sink), container.lock, container.driver)
        for event in sink.events:
            builder.feed(event)
        await remember(state)
        return JSONResponse(builder.response())

    sink_q = QueueSink()

    async def runner() -> None:
        try:
            state = await run_turn(turn, deps_for(sink_q), container.lock, container.driver)
            await remember(state)
            await sink_q.queue.put(None)
        except BaseException as exc:  # noqa: BLE001
            await sink_q.queue.put(exc)

    task = asyncio.create_task(runner())
    first = await sink_q.queue.get()
    if isinstance(first, BaseException):
        raise first

    async def stream() -> AsyncIterator[str]:
        backlog: list = [first]
        for line in builder.start():
            yield line
        try:
            while True:
                if backlog:
                    item = backlog.pop()
                else:
                    try:
                        item = await asyncio.wait_for(sink_q.queue.get(), KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                if item is None:
                    break
                if isinstance(item, BaseException):
                    err = item if isinstance(item, GatewayError) else GatewayError("The server had an error while processing your request.")
                    failed = builder.response("failed")
                    failed["error"] = {"code": err.code or "server_error", "message": err.message}
                    yield "event: response.failed\ndata: " + json.dumps({"type": "response.failed", "response": failed, "sequence_number": builder.seq}) + "\n\n"
                    break
                for line in builder.feed(item):
                    yield line
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
