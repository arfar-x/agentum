# Covers: FR-RTE-04, FR-RTE-07, FR-RTE-08, FR-RTE-09, FR-UPS-03
"""The pass-through: no path leaves the API prefix, restricted keys are refused, only safe headers travel."""

from __future__ import annotations

import asyncio
from urllib.parse import unquote

import httpx
import pytest
import uvicorn

from agent_gateway.domain.identity import KeyScope
from tests.conftest import KEY, completion

BODY = {"model": "some-model", "messages": [{"role": "user", "content": "hi"}]}

ESCAPES = [
    "/v1/../metrics",
    "/v1/./models",
    "/v1/%2e%2e/metrics",
    "/v1/%2E%2E/metrics",
    "/v1/.%2e/metrics",
    "/v1/%252e%252e/metrics",
    "/v1/%25252e%25252e/metrics",
    "/v1/models/..%2f..%2fmetrics",
    "/v1/models/%2e%2e%2fmetrics",
    "/v1/a/..%5cmetrics",
    "/v1/a%5cb",
    "/v1/models/%00",
    "/v1/models/x%0d%0ax",
    "/v1/a/..",
]


async def asgi_request(app, raw_path: str, method: str = "GET", headers: dict | None = None):
    """Drive the ASGI app the way uvicorn does: `path` percent-decoded once, `raw_path` as sent."""
    path, _, query = raw_path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": unquote(path), "raw_path": path.encode(), "query_string": query.encode(),
             "root_path": "", "server": ("gw", 80), "client": ("1.2.3.4", 1),
             "headers": [(k.lower().encode(), v.encode()) for k, v in {"authorization": f"Bearer {KEY}",
                                                                       **(headers or {})}.items()]}
    sent: list[dict] = []

    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()           # the client stays connected until the response is done

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body


@pytest.mark.parametrize("raw", ESCAPES)
async def test_a_path_that_could_leave_the_api_prefix_is_a_404_and_nothing_is_sent_upstream(make_gateway, raw):
    gw = await make_gateway()
    status, body = await asgi_request(gw.client._transport.app, raw)
    assert status == 404 and b'"error"' in body
    assert b"invalid_url" in body or b"Not Found" in body        # a newline in the path matches no route at all
    assert gw.upstream.seen == []


async def test_other_methods_and_the_model_retrieval_route_are_covered_too(make_gateway):
    gw = await make_gateway()
    app = gw.client._transport.app
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert (await asgi_request(app, "/v1/../metrics", method))[0] == 404
    assert (await asgi_request(app, "/v1/models/..%2f..%2fmetrics"))[0] == 404
    assert gw.upstream.seen == []


async def test_ordinary_paths_with_dots_colons_and_query_strings_still_pass(make_gateway):
    gw = await make_gateway()
    app = gw.client._transport.app
    for raw in ("/v1/files/report.v2.pdf", "/v1/models/ft:org:model-1", "/v1/audio/speech?x=1&y=2", "/v1/other"):
        assert (await asgi_request(app, raw))[0] == 200, raw
    seen = [path for _, path, _, _ in gw.upstream.seen]
    assert seen == ["/v1/files/report.v2.pdf", "/v1/models/ft:org:model-1", "/v1/audio/speech", "/v1/other"]


async def test_a_literal_dot_dot_over_a_real_socket_never_reaches_the_upstream(make_gateway):
    gw = await make_gateway()
    server = uvicorn.Server(uvicorn.Config(gw.client._transport.app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        for target in ("/v1/../metrics", "/v1/%2e%2e/metrics", "/v1/models/../../metrics"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"GET {target} HTTP/1.1\r\nHost: gw\r\nAuthorization: Bearer {KEY}\r\n"
                         "Connection: close\r\n\r\n".encode())
            await writer.drain()
            raw = await reader.read()
            writer.close()
            assert raw.startswith(b"HTTP/1.1 404"), (target, raw[:80])
            assert b"invalid_url" in raw
    finally:
        server.should_exit = True
        await task
    assert gw.upstream.seen == []


# ---- the key scope (FR-RTE-08)


async def test_a_restricted_key_cannot_use_the_pass_through_but_can_still_use_its_agents(make_gateway):
    gw = await make_gateway()
    keys = gw.container.keys
    keys.add_issued("alice", "agk_scoped", KeyScope(("router",), True))
    keys.add_issued("alice", "agk_readonly", KeyScope((), False))
    for bearer in ("agk_scoped", "agk_readonly"):
        for call in (gw.client.post("/v1/chat/completions", headers=gw.auth(bearer), json=BODY),
                     gw.client.post("/v1/responses", headers=gw.auth(bearer), json={"model": "some-model", "input": "x"}),
                     gw.client.get("/v1/models/some-model", headers=gw.auth(bearer)),
                     gw.client.get("/v1/other", headers=gw.auth(bearer))):
            r = await call
            assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    assert gw.upstream.seen == []
    gw.upstream.replies.append(completion("agent answer"))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth("agk_scoped"),
                             json={"model": "router", "messages": BODY["messages"]})
    assert r.status_code == 200


async def test_unrestricted_keys_and_tokens_keep_the_pass_through(make_gateway):
    from agent_gateway.application.identity_ports import Identity

    gw = await make_gateway()
    gw.container.keys.add_issued("alice", "agk_open", KeyScope())
    gw.tokens.known["tok.en.x"] = Identity("alice", "alice", (), ())
    for bearer in ("agk_open", "tok.en.x", KEY):
        assert (await gw.client.get("/v1/other", headers=gw.auth(bearer))).status_code == 200


async def test_a_read_only_model_key_login_is_refused_on_the_pass_through_and_a_full_one_is_not(make_gateway):
    mine = "sk-alice-model-key-9999"
    ro = await make_gateway(registered=())
    ro.container.keys.register("alice", mine)
    r = await ro.client.get("/v1/other", headers=ro.auth(mine))
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    assert ro.upstream.seen == []
    full = await make_gateway(registered=(), model_key_login="full")
    full.container.keys.register("alice", mine)
    assert (await full.client.get("/v1/other", headers=full.auth(mine))).status_code == 200


# ---- headers (FR-RTE-09)

FORBIDDEN = {"cookie", "x-agent-skills-env-jira_password", "x-env-jira_user", "x-kb-groups", "x-kb-trust",
             "x-forwarded-for", "x-api-key", "proxy-authorization", "x-custom"}


async def test_only_allow_listed_request_headers_reach_the_upstream_and_the_callers_token_never_does(make_gateway):
    gw = await make_gateway()
    sent = {"Cookie": "session=abc", "X-Agent-Skills-Env-JIRA_PASSWORD": "hunter2", "X-Env-JIRA_USER": "alice",
            "X-KB-Groups": "a,b", "X-KB-Trust": "s3cret", "X-Forwarded-For": "9.9.9.9", "X-Api-Key": "other",
            "Proxy-Authorization": "Basic x", "X-Custom": "1", "Connection": "keep-alive, X-Custom",
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "sdk/1.0",
            "OpenAI-Organization": "org-1", "X-Request-Id": "req-7"}
    gw.upstream.replies.append(completion("raw"))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(KEY, **sent), json=BODY)
    assert r.status_code == 200
    [(_, _, headers, _)] = gw.upstream.seen
    assert not FORBIDDEN & set(headers)
    assert headers["authorization"].startswith("Bearer sk-default-alice") and KEY not in repr(headers)
    assert (headers["content-type"], headers["accept"], headers["user-agent"]) == ("application/json", "application/json", "sdk/1.0")
    assert headers["openai-organization"] == "org-1" and headers["x-request-id"] == "req-7"
    assert "hunter2" not in repr(headers) and "s3cret" not in repr(headers)


async def test_hop_by_hop_and_cookie_headers_are_dropped_from_the_response(make_gateway):
    gw = await make_gateway()
    gw.upstream.reply_headers = {"Set-Cookie": "id=1", "WWW-Authenticate": "Bearer realm=x", "X-Upstream": "kept",
                                 "Keep-Alive": "timeout=5"}
    r = await gw.client.get("/v1/other", headers=gw.auth())
    assert r.status_code == 200
    assert "set-cookie" not in r.headers and "www-authenticate" not in r.headers and "keep-alive" not in r.headers
    assert r.headers["x-upstream"] == "kept"
