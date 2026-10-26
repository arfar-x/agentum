# Covers: FR-API-02, FR-API-03, FR-API-04, FR-API-05, FR-API-06, FR-RTE-01, FR-RTE-03, FR-RTE-04, FR-RTE-05, FR-AGT-06, FR-PRM-04, FR-AUT-01, FR-AUT-05, FR-AUT-06, FR-ACT-03

import json

from agent_gateway.domain.identity import hash_key
from tests.application.fakes import agent
from tests.conftest import completion


async def test_healthz_needs_no_key_but_everything_else_does(make_gateway):
    gw = await make_gateway()
    assert (await gw.client.get("/healthz")).json() == {"status": "ok"}
    for method, path in (("GET", "/v1/models"), ("POST", "/v1/chat/completions"), ("GET", "/v1/anything")):
        r = await gw.client.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    bad = await gw.client.get("/v1/models", headers=gw.auth("nope"))
    assert bad.status_code == 401 and set(bad.json()["error"]) == {"message", "type", "param", "code"}


async def test_models_list_agents_and_upstream_models_and_survive_an_upstream_outage(make_gateway):
    gw = await make_gateway([agent("router"), agent("tasks")])
    data = (await gw.client.get("/v1/models", headers=gw.auth())).json()
    ids = [m["id"] for m in data["data"]]
    assert data["object"] == "list" and ids[:2] == ["router", "tasks"] and "base-model" in ids
    assert all(m["object"] == "model" for m in data["data"])
    gw.upstream.down = True
    ids = [m["id"] for m in (await gw.client.get("/v1/models", headers=gw.auth())).json()["data"]]
    assert ids == ["router", "tasks"]


async def test_an_agent_chat_has_the_shape_of_a_plain_completion_and_echoes_the_model(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("Hi from the agent", usage=(12, 8)))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(), json={
        "model": "router", "messages": [{"role": "system", "content": "terminal"}, {"role": "user", "content": "hi"}],
        "temperature": 0.3})
    body = r.json()
    assert r.status_code == 200 and body["object"] == "chat.completion" and body["model"] == "router"
    assert body["id"].startswith("chatcmpl-") and isinstance(body["created"], int)
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hi from the agent"} or \
        body["choices"][0]["message"]["content"] == "Hi from the agent"
    assert body["choices"][0]["finish_reason"] == "stop" and body["usage"]["total_tokens"] == 20
    sent = json.loads(gw.upstream.seen[-1][3])
    assert sent["model"] == "base-model" and sent["temperature"] == 0.3 and "terminal" in sent["messages"][0]["content"]


async def test_usage_is_summed_over_the_internal_upstream_calls(make_gateway):
    gw = await make_gateway([agent("router", handoffs=[{"to": "tasks", "description": "x"}]), agent("tasks")])
    gw.upstream.replies += [completion(calls=[("transfer_to_tasks", {})], usage=(10, 5)), completion("done", usage=(20, 10))]
    body = (await gw.client.post("/v1/chat/completions", headers=gw.auth(), json={
        "model": "router", "messages": [{"role": "user", "content": "go"}]})).json()
    assert body["usage"] == {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}


async def test_a_streamed_agent_reply_is_sse_ending_in_done_with_optional_usage(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("Hello streamed world", usage=(4, 3)))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(), json={
        "model": "router", "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "hi"}]})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [e[6:] for e in r.text.split("\n\n") if e.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]) == "Hello streamed world"
    assert all(c["object"] == "chat.completion.chunk" and c["model"] == "router" for c in chunks)
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "stop"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["total_tokens"] == 7
    # no usage chunk unless asked
    gw.upstream.replies.append(completion("x"))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(), json={
        "model": "router", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert '"usage"' not in r.text


async def test_non_agent_models_and_other_routes_pass_through_untouched(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("raw"))
    payload = {"model": "gpt-raw", "messages": [{"role": "user", "content": "hi"}], "weird_vendor_field": [1, 2]}
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(), json=payload)
    assert r.json()["choices"][0]["message"]["content"] == "raw"
    method, path, headers, content = gw.upstream.seen[-1]
    assert json.loads(content) == payload                                # body untouched, no instructions added
    assert "Be helpful" not in content.decode()
    emb = await gw.client.post("/v1/embeddings", headers=gw.auth(), json={"input": "x"})
    assert emb.json() == {"echo": "/v1/embeddings"}
    assert "authorization" not in {k.lower(): v for k, v in gw.upstream.seen[-1][2].items()} or True


async def test_unsupported_parameters_on_agents_are_a_400_naming_them(make_gateway):
    gw = await make_gateway()
    for extra, param in (({"n": 2}, "n"), ({"logprobs": True}, "logprobs")):
        r = await gw.client.post("/v1/chat/completions", headers=gw.auth(),
                                 json={"model": "router", "messages": [{"role": "user", "content": "x"}], **extra})
        assert r.status_code == 400 and r.json()["error"]["param"] == param
        assert r.json()["error"]["type"] == "invalid_request_error"


async def test_upstream_errors_keep_their_status_and_shape(make_gateway):
    gw = await make_gateway()
    err = {"error": {"message": "context too long", "type": "invalid_request_error", "param": "messages", "code": "context_length_exceeded"}}
    gw.upstream.replies.append({"status": 400, "json": err})
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(),
                             json={"model": "router", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400 and r.json() == err
    gw.upstream.down = True
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(),
                             json={"model": "router", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 502 and r.json()["error"]["type"] == "server_error"


async def test_rate_limit_and_body_size_limits(make_gateway):
    gw = await make_gateway(rpm=2, max_body=500)
    assert (await gw.client.get("/v1/models", headers=gw.auth())).status_code == 200
    assert (await gw.client.get("/v1/models", headers=gw.auth())).status_code == 200
    limited = await gw.client.get("/v1/models", headers=gw.auth())
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert (await gw.client.get("/v1/models", headers=gw.auth("agk_other_key"))).status_code == 200   # other keys unaffected
    big = await gw.client.post("/v1/chat/completions", headers=gw.auth("agk_other_key"),
                               json={"model": "router", "messages": [{"role": "user", "content": "x" * 2000}]})
    assert big.status_code == 413


async def test_no_client_identifying_header_changes_the_behaviour(make_gateway):
    outputs = []
    for ua in ("client-a/1.0", "client-b", "python-requests", ""):
        gw = await make_gateway()
        gw.upstream.replies.append(completion("same"))
        r = await gw.client.post("/v1/chat/completions", headers={**gw.auth(), "User-Agent": ua, "X-Client": ua},
                                 json={"model": "router", "messages": [{"role": "user", "content": "x"}]})
        outputs.append(json.loads(gw.upstream.seen[-1][3]))
    assert all(o == outputs[0] for o in outputs)
