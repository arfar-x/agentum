"""Streaming reverse proxy to the upstream, body bytes untouched."""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from agent_gateway.adapters.inbound.openai_http.auth import Caller
from agent_gateway.application.upstream_credentials import REFUSED
from agent_gateway.domain.errors import UpstreamUnavailable
from agent_gateway.domain.identity import Principal

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length", "authorization"}
RESPONSE_DROP = {"connection", "keep-alive", "transfer-encoding", "content-length"}


async def forward(request: Request, path: str, caller: Caller, body: bytes | None = None) -> Response:
    """Send the request upstream with the caller's model key; their own Authorization never goes."""
    container = request.app.state.container
    person = Principal(user_id=caller.user_id, username=caller.user_id)
    key = await container.credentials.resolve(person)          # nothing is sent when there is none
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    headers["authorization"] = f"Bearer {key.secret}"
    if body is None:
        body = await request.body()
    upstream_request = container.upstream.build_request(
        request.method, "/" + path.lstrip("/"), headers=headers, params=request.query_params, content=body or None)
    try:
        upstream = await container.upstream.send(upstream_request, stream=True)
    except httpx.TransportError as exc:
        raise UpstreamUnavailable(f"upstream unreachable: {exc}") from exc
    if upstream.status_code in REFUSED:
        await upstream.aclose()
        raise await container.credentials.rejected(person, key, upstream.status_code)
    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() not in RESPONSE_DROP},
        background=BackgroundTask(upstream.aclose))
