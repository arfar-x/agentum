"""``/v1/models``: the upstream's models and the agents."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request

from agent_gateway.adapters.inbound.openai_http.auth import authenticate
from agent_gateway.adapters.inbound.openai_http.proxy import forward
from agent_gateway.domain.context import context_fields, context_window_for

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


async def _upstream_models(request: Request) -> list[dict[str, Any]]:
    container = request.app.state.container
    headers = {"authorization": f"Bearer {container.upstream_api_key}"} if container.upstream_api_key else {}
    try:
        response = await container.upstream.get("/models", headers=headers)
        response.raise_for_status()
        return list(response.json().get("data", []))
    except (httpx.HTTPError, ValueError):
        return []


@router.get("/v1/models")
async def list_models(request: Request, _=Depends(authenticate)) -> dict[str, Any]:
    container = request.app.state.container
    data = [agent_entry(a, container) for a in container.config.agents.values()]
    data += [upstream_entry(e, container) for e in await _upstream_models(request)]
    return {"object": "list", "data": data}


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str, request: Request, _=Depends(authenticate)):
    container = request.app.state.container
    if model_id in container.config.agents:
        return agent_entry(container.config.agents[model_id], container)
    return await forward(request, f"models/{model_id}")
