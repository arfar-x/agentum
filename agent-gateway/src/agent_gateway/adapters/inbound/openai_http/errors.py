"""OpenAI-shaped errors."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_gateway.domain.errors import GatewayError, UpstreamRejected

log = logging.getLogger("agent_gateway")


def error_body(message: str, type_: str, param: str | None = None, code: str | None = None) -> dict:
    return {"error": {"message": message, "type": type_, "param": param, "code": code}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(UpstreamRejected)
    async def upstream_rejected(_: Request, exc: UpstreamRejected) -> JSONResponse:
        body = exc.body if isinstance(exc.body, dict) and "error" in exc.body else error_body(exc.message, exc.type, exc.param, exc.code)
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        retry = getattr(exc, "retry_after", None)
        return JSONResponse(error_body(exc.message, exc.type, exc.param, exc.code), status_code=exc.status,
                            headers={"Retry-After": str(retry)} if retry else None)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return JSONResponse(error_body(f"Invalid request: {first.get('msg', 'invalid')}", "invalid_request_error", loc or None),
                            status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        kind = "invalid_request_error" if exc.status_code < 500 else "server_error"
        return JSONResponse(error_body(str(exc.detail), kind), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")           # details stay in the log, not in the response
        return JSONResponse(error_body("The server had an error while processing your request.", "server_error"),
                            status_code=500)
