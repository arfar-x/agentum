"""Streaming reverse proxy to the upstream, body bytes untouched."""

from __future__ import annotations

import posixpath
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from agent_gateway.adapters.inbound.openai_http.auth import Caller
from agent_gateway.application.upstream_credentials import REFUSED
from agent_gateway.domain.errors import InvalidUrl, PermissionDenied, UpstreamUnavailable
from agent_gateway.domain.identity import Principal

# The only request headers that reach the model provider. Everything else -- the caller's own
# Authorization, cookies, tool-credential headers (X-Agent-Skills-Env-*, X-Env-*), X-KB-*,
# X-Forwarded-* -- stays here.
FORWARDED_REQUEST_HEADERS = frozenset({
    "content-type", "accept", "user-agent", "openai-organization", "openai-project", "openai-beta",
    "x-request-id", "x-client-request-id"})
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                        "trailer", "transfer-encoding", "upgrade", "host", "content-length"})
RESPONSE_DROP = HOP_BY_HOP | {"set-cookie", "www-authenticate"}
MAX_DECODES = 4


def _decodings(text: str) -> list[str]:
    """`text` and every further percent-decoding of it (stops at a fixed point or after a few rounds)."""
    forms = [text]
    for _ in range(MAX_DECODES):
        decoded = unquote(forms[-1])
        if decoded == forms[-1]:
            break
        forms.append(decoded)
    return forms


def safe_path(path: str, raw_path: str = "") -> bool:
    """False when the path could climb out of the upstream's API prefix: a `.` or `..` segment in
    any (raw, decoded or repeatedly decoded) form, a backslash, a NUL or another control character."""
    for form in _decodings(path) + _decodings(raw_path):
        if "\\" in form or any(ord(c) < 32 or ord(c) == 127 for c in form):
            return False
        if any(segment in (".", "..") for segment in form.split("/")):
            return False
    return True


def _under(base: httpx.URL, built: httpx.URL) -> bool:
    """The request URL stays below the upstream base path once normalised."""
    prefix = base.path.rstrip("/")
    target = posixpath.normpath(urlsplit(str(built)).path)
    return target.startswith(prefix + "/") if prefix else target.startswith("/")


def _refuse_restricted(caller: Caller) -> None:
    """A key limited to some agents, or to read-only use, is not allowed raw model access."""
    scope = caller.scope
    if scope.agents or not scope.write:
        raise PermissionDenied("This key is limited and cannot call the model directly. "
                               "Use an agent it is allowed to use.")


async def forward(request: Request, path: str, caller: Caller, body: bytes | None = None) -> Response:
    """Send the request upstream with the caller's model key; their own Authorization never goes."""
    container = request.app.state.container
    _refuse_restricted(caller)
    if not safe_path(path, request.scope.get("raw_path", b"").decode("latin-1")):
        raise InvalidUrl(f"Invalid URL ({request.method} {request.url.path})")
    person = Principal(user_id=caller.user_id, username=caller.user_id)
    key = await container.credentials.resolve(person)          # nothing is sent when there is none
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARDED_REQUEST_HEADERS}
    headers["authorization"] = f"Bearer {key.secret}"
    if body is None:
        body = await request.body()
    upstream_request = container.upstream.build_request(
        request.method, "/" + quote(path.lstrip("/"), safe="/:@!$&'()*+,;=-._~"), headers=headers,
        params=request.query_params, content=body or None)
    if not _under(container.upstream.base_url, upstream_request.url):
        raise InvalidUrl(f"Invalid URL ({request.method} {request.url.path})")
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
