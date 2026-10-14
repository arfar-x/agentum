"""Start the HTTP server."""

from __future__ import annotations

import uvicorn

from agent_gateway.adapters.inbound.openai_http.app import create_app
from agent_gateway.config import EnvSettings
from agent_gateway.container import build_container


async def serve(env: EnvSettings) -> None:
    container = await build_container(env)
    config = uvicorn.Config(create_app(container), host=env.host, port=env.port, log_level="info", proxy_headers=True)
    await uvicorn.Server(config).serve()
