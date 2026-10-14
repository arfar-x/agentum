"""``/v1/models``: the upstream's models and the agents."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request

from agent_gateway.adapters.inbound.openai_http.auth import authenticate
from agent_gateway.adapters.inbound.openai_http.proxy import forward

router = APIRouter()
CREATED = int(time.time())


def agent_entry(agent) -> dict[str, Any]:
    return {"id": agent.id, "object": "model", "created": CREATED, "owned_by": "agent-gateway"}


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
    agents = request.app.state.container.config.agents
    data = [agent_entry(a) for a in agents.values()] + await _upstream_models(request)
    return {"object": "list", "data": data}


@router.get("/v1/models/{model_id:path}")
async def get_model(model_id: str, request: Request, _=Depends(authenticate)):
    agents = request.app.state.container.config.agents
    if model_id in agents:
        return agent_entry(agents[model_id])
    return await forward(request, f"models/{model_id}")
