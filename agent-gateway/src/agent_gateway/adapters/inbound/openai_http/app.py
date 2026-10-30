"""The FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request

from agent_gateway.adapters.inbound.openai_http import background, chat, models, responses
from agent_gateway.adapters.inbound.openai_http.auth import Caller, authenticate
from agent_gateway.adapters.inbound.openai_http.errors import install_error_handlers
from agent_gateway.adapters.inbound.openai_http.proxy import forward


def create_app(container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks = await background.start(container)
        yield
        await background.stop(tasks)
        await container.close()

    app = FastAPI(title="agent-gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.container = container
    install_error_handlers(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    portal = getattr(container, "portal", None)
    if portal is not None:                      # PORTAL_BASE_URL is set (FR-PRT-01)
        from agent_gateway.adapters.inbound.portal import create_portal_app

        app.mount("/portal", create_portal_app(portal))

    app.include_router(models.router)
    app.include_router(chat.router)
    app.include_router(responses.router)

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def passthrough(path: str, request: Request, caller: Caller = Depends(authenticate)):
        return await forward(request, path, caller)

    return app
