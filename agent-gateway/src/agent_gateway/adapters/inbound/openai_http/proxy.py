"""Streaming reverse proxy to the upstream, body bytes untouched."""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from agent_gateway.domain.errors import UpstreamUnavailable

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length", "authorization"}
RESPONSE_DROP = {"connection", "keep-alive", "transfer-encoding", "content-length"}


async def forward(request: Request, path: str, body: bytes | None = None) -> Response:
    container = request.app.state.container
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    if container.upstream_api_key:
        headers["authorization"] = f"Bearer {container.upstream_api_key}"
    if body is None:
        body = await request.body()
    upstream_request = container.upstream.build_request(
        request.method, "/" + path.lstrip("/"), headers=headers, params=request.query_params, content=body or None)
    try:
        upstream = await container.upstream.send(upstream_request, stream=True)
    except httpx.TransportError as exc:
        raise UpstreamUnavailable(f"upstream unreachable: {exc}") from exc
    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items() if k.lower() not in RESPONSE_DROP},
        background=BackgroundTask(upstream.aclose))
