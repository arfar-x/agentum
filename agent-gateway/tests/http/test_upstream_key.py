# Covers: FR-UPS-01, FR-UPS-02, FR-UPS-03, FR-UPS-04, FR-UPS-05, FR-UPS-07

"""The model key a request is made with, end to end over HTTP against a stub that records Authorization."""

import logging

from tests.conftest import KEY, KEY2, SHARED_KEY, completion

ALICE_KEY = "sk-alice-own-model-key-1111"
BOB_KEY = "sk-bob-own-model-key-2222"
HI = [{"role": "user", "content": "hi"}]


def chat(gw, model, key=KEY, **extra):
    return gw.client.post("/v1/chat/completions", headers=gw.auth(key),
                          json={"model": model, "messages": HI, **extra})


def sent(gw):
    return [h.get("authorization") for _, path, h, _ in gw.upstream.seen if path.endswith("/chat/completions")]


async def test_two_people_reach_the_upstream_with_their_own_keys_for_turns_and_pass_through(make_gateway):
    gw = await make_gateway()
    gw.container.keys.register("alice", ALICE_KEY)
    gw.container.keys.register("bob", BOB_KEY)
    gw.upstream.replies += [completion("a"), completion("b"), completion("c"), completion("d")]
    await chat(gw, "router", KEY)
    await chat(gw, "router", KEY2)
    await chat(gw, "some-model", KEY)
    await chat(gw, "some-model", KEY2)
    assert sent(gw) == [f"Bearer {ALICE_KEY}", f"Bearer {BOB_KEY}"] * 2


async def test_a_person_without_a_key_uses_the_shared_one_for_turns_pass_through_and_listing(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies += [completion("a"), completion("b"), completion("c")]
    await chat(gw, "router")
    await chat(gw, "some-model")
    await gw.client.get("/v1/models", headers=gw.auth())
    assert {h.get("authorization") for _, _, h, _ in gw.upstream.seen} == {f"Bearer {SHARED_KEY}"}
    assert len(gw.upstream.seen) == 3


async def test_with_no_key_at_all_the_call_fails_clearly_and_nothing_is_sent(make_gateway):
    gw = await make_gateway(shared_key="")
    for model in ("router", "some-model"):
        r = await chat(gw, model)
        err = r.json()["error"]
        assert r.status_code == 403 and err["code"] == "upstream_key_required"
        assert "https://portal.example" in err["message"]
    listing = await gw.client.get("/v1/models", headers=gw.auth())
    assert [m["id"] for m in listing.json()["data"]] == ["router"]
    assert gw.upstream.seen == []


async def test_the_pass_through_replaces_the_callers_authorization_and_never_forwards_it(make_gateway):
    gw = await make_gateway(shared_key="")
    gw.container.keys.register("alice", ALICE_KEY)
    gw.upstream.replies += [completion("x")]
    await chat(gw, "some-model", KEY)                        # the bearer is a gateway key, not the model key
    await gw.client.get("/v1/other", headers=gw.auth(KEY))
    for _, _, headers, _ in gw.upstream.seen:
        assert headers["authorization"] == f"Bearer {ALICE_KEY}"
    assert KEY not in repr(gw.upstream.seen)


async def test_a_registered_key_used_as_the_bearer_is_the_key_used_upstream(make_gateway):
    gw = await make_gateway(shared_key="")
    gw.container.keys.register("alice", ALICE_KEY)
    gw.upstream.replies += [completion("a"), completion("b")]
    await chat(gw, "router", ALICE_KEY)
    await chat(gw, "some-model", ALICE_KEY)
    assert sent(gw) == [f"Bearer {ALICE_KEY}"] * 2


async def test_a_refused_personal_key_is_a_502_naming_the_fix_and_is_audited_without_the_key(make_gateway, caplog):
    gw = await make_gateway()
    gw.container.keys.register("alice", ALICE_KEY)
    refusal = {"status": 401, "json": {"error": {"message": f"Incorrect API key provided: {ALICE_KEY}"}}}
    gw.upstream.replies += [refusal, refusal]
    with caplog.at_level(logging.DEBUG):
        responses = [await chat(gw, "router", KEY), await chat(gw, "some-model", KEY)]
    for r in responses:
        assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_key_rejected"
        assert "https://portal.example" in r.json()["error"]["message"]
        assert ALICE_KEY not in r.text
    [event] = gw.audit.events
    assert event.action == "upstream.key_rejected" and event.actor == "alice" and event.count == 2
    assert ALICE_KEY not in repr(event) and ALICE_KEY not in caplog.text


async def test_a_refused_key_is_not_echoed_when_the_upstream_fails_another_way(make_gateway, caplog):
    gw = await make_gateway()
    gw.container.keys.register("alice", ALICE_KEY)
    gw.upstream.replies += [{"status": 400, "json": {"error": {"message": f"bad request for {ALICE_KEY}"}}}]
    with caplog.at_level(logging.DEBUG):
        r = await chat(gw, "router", KEY)
    assert r.status_code == 400 and ALICE_KEY not in r.text and ALICE_KEY not in caplog.text
