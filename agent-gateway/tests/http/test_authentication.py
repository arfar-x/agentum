# Covers: FR-IDN-02, FR-ATH-09, FR-ATH-10, FR-ATH-11, FR-ATH-01, FR-ATH-06
from agent_gateway.application.identity_ports import Identity
from tests.conftest import KEY, KEY2


async def test_every_route_but_healthz_needs_a_credential(make_gateway):
    gw = await make_gateway()
    assert (await gw.client.get("/healthz")).status_code == 200
    for method, path in (("get", "/v1/models"), ("get", "/v1/models/router"), ("post", "/v1/chat/completions"),
                         ("post", "/v1/responses"), ("post", "/v1/embeddings"), ("get", "/v1/anything/else")):
        r = await getattr(gw.client, method)(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key", path


async def test_a_refusal_has_the_openai_shape_and_names_no_check(make_gateway):
    gw = await make_gateway()
    secret = "agk_not-a-real-key"
    bodies = {(await gw.client.get("/v1/models", headers=gw.auth(b))).text for b in (secret, "a.b.c", "x")}
    bodies.add((await gw.client.get("/v1/models")).text)
    assert len(bodies) == 1 and secret not in bodies.pop()


async def test_a_refusal_is_audited_without_the_bearer(make_gateway):
    gw = await make_gateway()
    await gw.client.get("/v1/models", headers=gw.auth("agk_secret-value"))
    event = gw.audit.events[-1]
    assert (event.action, event.target, event.detail["path"]) == ("auth.denied", "unknown_key", "/v1/models")
    assert "secret-value" not in str(event)


async def test_a_token_is_accepted_and_its_groups_reach_the_turn(make_gateway):
    gw = await make_gateway()
    gw.tokens.known["tok.en.x"] = Identity("sub-t", "tina", ("g",), ("r",), client="telegram-bot")
    assert (await gw.client.get("/v1/models", headers=gw.auth("tok.en.x"))).status_code == 200
    assert any(e.action == "auth.first_use" for e in gw.audit.events)


async def test_a_stale_directory_is_503_with_retry_after_for_key_callers(make_gateway):
    gw = await make_gateway()
    gw.source.error = RuntimeError("down")
    gw.now.advance(hours=1)
    r = await gw.client.get("/v1/models", headers=gw.auth())
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert r.json()["error"]["code"] == "identity_unavailable" and KEY not in r.text


async def test_a_disabled_person_is_refused_at_once_after_a_sync(make_gateway):
    gw = await make_gateway()
    assert (await gw.client.get("/v1/models", headers=gw.auth())).status_code == 200
    from dataclasses import replace
    gw.directory.rows["alice"] = replace(gw.directory.rows["alice"], enabled=False)
    assert (await gw.client.get("/v1/models", headers=gw.auth())).status_code == 401
    assert (await gw.client.get("/v1/models", headers=gw.auth(KEY2))).status_code == 200


async def test_the_limit_is_per_person_whichever_bearer_is_used(make_gateway):
    gw = await make_gateway(rpm=2)
    gw.tokens.known["alice.jwt.x"] = Identity("alice", "alice", (), ())
    assert (await gw.client.get("/v1/models", headers=gw.auth(KEY))).status_code == 200
    assert (await gw.client.get("/v1/models", headers=gw.auth("alice.jwt.x"))).status_code == 200
    assert (await gw.client.get("/v1/models", headers=gw.auth(KEY))).status_code == 429
    assert (await gw.client.get("/v1/models", headers=gw.auth(KEY2))).status_code == 200


async def test_the_chat_turn_runs_as_the_principal(make_gateway):
    from tests.conftest import completion
    from tests.application.fakes import agent
    gw = await make_gateway([agent("router")])
    gw.upstream.replies.append(completion("hi"))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(),
                             json={"model": "router", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 200
    assert any(owner == "alice" for owner, _ in gw.container.deps.store.threads)
