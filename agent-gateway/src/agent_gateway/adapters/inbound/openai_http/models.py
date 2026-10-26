"""``/v1/models``: the upstream's models and the agents."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request

from agent_gateway.adapters.inbound.openai_http.auth import Caller, authenticate
from agent_gateway.adapters.inbound.openai_http.proxy import forward
from agent_gateway.application.upstream_credentials import REFUSED
from agent_gateway.domain.access import can_use, visible_agents
from agent_gateway.domain.context import context_fields, context_window_for
from agent_gateway.domain.errors import ModelNotFound, UpstreamKeyRequired
from agent_gateway.domain.identity import Principal

router = APIRouter()
CREATED = int(time.time())


def agent_entry(agent, container) -> dict[str, Any]:
    tokens = context_window_for(agent.model, agent=agent, per_model=container.config.context_windows,
                                default=container.context_window)
    return {"id": agent.id, "object": "model", "created": CREATED, "owned_by": "agent-gateway", **context_fields(tokens)}


def upstream_entry(entry: dict[str, Any], container) -> dict[str, Any]:
    """The upstream's own entry, plus a context window when it did not state one."""
    if any(k in entry for k in ("context_window", "context_length", "max_model_len")):
        return entry
    tokens = context_window_for(str(entry.get("id")), per_model=container.config.context_windows,
                                default=container.context_window)
    return {**entry, **context_fields(tokens)}


async def _upstream_models(request: Request, caller: Caller) -> list[dict[str, Any]]:
    """The upstream's models as this person sees them; none when they have no key or it is refused."""
    container = request.app.state.container
    person = Principal(user_id=caller.user_id, username=caller.user_id)
    try:
        key = await container.credentials.resolve(person)
    except UpstreamKeyRequired:
        return []
    try:
        response = await container.upstream.get("/models", headers={"authorization": f"Bearer {key.secret}"})
        if response.status_code in REFUSED:
            await container.credentials.rejected(person, key, response.status_code)
        response.raise_for_status()
        return list(response.json().get("data", []))
    except (httpx.HTTPError, ValueError):
        return []


@router.get("/v1/models")
async def list_models(request: Request, caller: Caller = Depends(authenticate)) -> dict[str, Any]:
    container = request.app.state.container
    data = [agent_entry(a, container) for a in visible_agents(caller, container.config.agents)]
    data += [upstream_entry(e, container) for e in await _upstream_models(request, caller)]
    return {"object": "list", "data": data}


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str, request: Request, caller: Caller = Depends(authenticate)):
    container = request.app.state.container
    if model_id in container.config.agents:
        agent = container.config.agents[model_id]
        if not can_use(caller, agent):
            raise ModelNotFound(model_id)
        return agent_entry(agent, container)
    return await forward(request, f"models/{model_id}", caller)
