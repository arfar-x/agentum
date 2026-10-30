# Covers: FR-ATH-13, FR-ATH-14, FR-UPS-08, FR-CRD-03, FR-KEY-03
"""Two people with the same model-key string, over HTTP: separate credentials, own turns, no login by the shared string."""

import json

from tests.application.fakes import FakeCreds, agent, spec
from tests.conftest import KEY, KEY2, completion

SHARED = "sk-same-string-registered-by-both-0001"
ALICE_NEW = "sk-alice-replaced-key-0002"
TOOLS = [{"server": "skills", "include": ["jira_*"], "approval": ["jira_create_issue"]}]
SPECS = {"skills": [spec("jira_search", query={"type": "string"}),
                    spec("jira_create_issue", summary={"type": "string"}, confirm={"type": "boolean"})]}
MSG = [{"role": "user", "content": "go"}]


async def gateway(make_gateway, **kw):
    creds = FakeCreds({("alice", "skills"): {"JIRA_USER": "alice-jira"}, ("bob", "skills"): {"JIRA_USER": "bob-jira"}})
    gw = await make_gateway([agent("worker", tools=TOOLS)], specs=SPECS, creds=creds, registered=(), **kw)
    gw.container.keys.register("alice", SHARED)
    gw.container.keys.register("bob", SHARED)
    return gw


def chat(gw, bearer, model="worker"):
    return gw.client.post("/v1/chat/completions", headers=gw.auth(bearer), json={"model": model, "messages": MSG})


def auth_of(gw):
    return [h.get("authorization") for _, p, h, _ in gw.upstream.seen if p.endswith("/chat/completions")]


def tool_names(gw):
    return [t["function"]["name"] for t in json.loads(gw.upstream.seen[-1][3]).get("tools", [])]


async def test_each_person_runs_with_their_own_stored_credentials_and_own_model_key(make_gateway):
    gw = await gateway(make_gateway)
    for bearer in (KEY, KEY2):
        gw.upstream.replies += [completion(calls=[("jira_search", {"query": "x"})]), completion("done")]
        assert (await chat(gw, bearer)).status_code == 200
    assert [c[3] for c in gw.tools.calls] == [{"JIRA_USER": "alice-jira"}, {"JIRA_USER": "bob-jira"}]
    assert set(auth_of(gw)) == {f"Bearer {SHARED}"}
    # alice replaces hers; bob's calls still use the string bob registered, alice's use her new one
    gw.container.keys.register("alice", ALICE_NEW)
    gw.upstream.replies += [completion("a"), completion("b")]
    gw.upstream.seen.clear()
    await chat(gw, KEY)
    await chat(gw, KEY2)
    assert auth_of(gw) == [f"Bearer {ALICE_NEW}", f"Bearer {SHARED}"]


async def test_the_shared_string_as_bearer_is_401_with_the_specific_message_and_audit_reason(make_gateway):
    gw = await gateway(make_gateway)
    r = await gw.client.get("/v1/models", headers=gw.auth(SHARED))
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "model_key_shared"
    assert err["message"] == ("This key is shared by several people, so it cannot identify you. "
                              "Sign in with Keycloak or use a gateway key.")
    assert SHARED not in r.text
    assert gw.audit.events[-1].target == "model_key_shared"
    other = await gw.client.get("/v1/models", headers=gw.auth("sk-unknown-string"))
    assert other.status_code == 401 and other.json()["error"]["code"] == "invalid_api_key"
    assert gw.upstream.seen == []


async def test_once_one_holder_revokes_the_other_logs_in_read_only_and_gets_no_write_tools(make_gateway):
    gw = await gateway(make_gateway)
    gw.container.keys.revoke_model_key("alice")
    gw.upstream.replies += [completion("ok")]
    assert (await chat(gw, SHARED)).status_code == 200
    assert tool_names(gw) == ["jira_search"]                  # approval-gated write tool never offered
    gw.upstream.replies += [completion("ok")]
    await chat(gw, KEY2)                                      # bob with an issued key keeps his full scope
    assert tool_names(gw) == ["jira_search", "jira_create_issue"]


async def test_full_mode_offers_write_tools_and_off_mode_refuses(make_gateway):
    creds = FakeCreds({("alice", "skills"): {"JIRA_USER": "alice-jira"}})
    full = await make_gateway([agent("worker", tools=TOOLS)], specs=SPECS, creds=creds, registered=(),
                              model_key_login="full")
    full.container.keys.register("alice", SHARED)
    full.upstream.replies += [completion("ok")]
    assert (await chat(full, SHARED)).status_code == 200 and tool_names(full) == ["jira_search", "jira_create_issue"]
    off = await make_gateway([agent("worker", tools=TOOLS)], specs=SPECS, registered=(), model_key_login="off")
    off.container.keys.register("alice", SHARED)
    assert (await chat(off, SHARED)).status_code == 401
