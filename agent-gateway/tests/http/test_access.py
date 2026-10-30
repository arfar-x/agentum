# Covers: FR-ENT-03, FR-ENT-04, FR-ENT-05, FR-ENT-06, FR-ENT-07, FR-ENT-08, FR-ENT-09, FR-ENT-10

import json
import re

from fastapi import Request

from agent_gateway.adapters.inbound.openai_http.auth import authenticate
from agent_gateway.domain.identity import KeyScope, Principal
from tests.application.fakes import agent, spec
from tests.conftest import completion

TOOLS = [{"server": "skills", "include": ["jira_*"], "approval": ["jira_create_issue"]}]
SPECS = {"skills": [spec("jira_search", query={"type": "string"}),
                    spec("jira_create_issue", summary={"type": "string"}, confirm={"type": "boolean"})]}

# bearer -> principal; the real front door builds these from tokens and keys
PEOPLE: dict[str, Principal] = {}


def person(name, groups=(), roles=(), **scope):
    PEOPLE[name] = Principal(user_id=name, username=name, groups=tuple(groups), roles=tuple(roles), scope=KeyScope(**scope))
    return name


async def _gateway(make_gateway, agents):
    gw = await make_gateway(agents, specs=SPECS)

    async def resolve(request: Request) -> Principal:
        bearer = request.headers["authorization"].split(" ", 1)[1]
        principal = PEOPLE[bearer]
        if principal.user_id not in gw.container.keys.upstream:
            gw.container.keys.register(principal.user_id, f"sk-model-{principal.user_id}-0000")
        request.state.principal = principal
        return principal

    gw.client._transport.app.dependency_overrides[authenticate] = resolve
    return gw


def agents():
    return [
        agent("open"),
        agent("product", access={"groups": ["product"], "roles": ["analyst"]}),
        agent("writer", tools=TOOLS, access={"groups": ["product"], "write_groups": ["leads"]}),
    ]


async def ids(gw, who):
    data = (await gw.client.get("/v1/models", headers=gw.auth(who))).json()["data"]
    return [m["id"] for m in data]


async def test_models_list_only_what_the_caller_may_use_and_keeps_upstream_models(make_gateway):
    gw = await _gateway(make_gateway, agents())
    assert await ids(gw, person("nobody")) == ["open", "base-model"]
    assert await ids(gw, person("member", ["product"])) == ["open", "product", "writer", "base-model"]
    assert await ids(gw, person("analyst", roles=["analyst"])) == ["open", "product", "base-model"]


async def test_a_key_scope_narrows_the_list_to_the_intersection(make_gateway):
    gw = await _gateway(make_gateway, agents())
    got = await ids(gw, person("scoped", ["product"], agents=("product", "ghost")))
    assert [i for i in got if i != "base-model"] == ["product"]


async def test_naming_a_refused_agent_is_the_not_found_error_and_pass_through_is_not_gated(make_gateway):
    gw = await _gateway(make_gateway, agents())
    body = {"model": "product", "messages": [{"role": "user", "content": "hi"}]}
    for path, payload in (("/v1/chat/completions", body), ("/v1/responses", {"model": "product", "input": "hi"})):
        r = await gw.client.post(path, headers=gw.auth(person("nobody")), json=payload)
        assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
        assert set(r.json()["error"]) == {"message", "type", "param", "code"}
    assert (await gw.client.get("/v1/models/product", headers=gw.auth("nobody"))).status_code == 404
    gw.upstream.replies.append(completion("raw"))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth("nobody"),
                             json={"model": "gpt-raw", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    gw.upstream.replies.append(completion("fine"))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(person("member", ["product"])), json=body)
    assert r.status_code == 200


def _tool_names(gw):
    return [t["function"]["name"] for t in json.loads(gw.upstream.seen[-1][3]).get("tools", [])]


async def test_write_tools_are_hidden_from_a_reader_and_a_read_only_key(make_gateway):
    gw = await _gateway(make_gateway, agents())
    chat = {"model": "writer", "messages": [{"role": "user", "content": "hi"}]}
    headers = {"X-Env-JIRA_USER": "alice"}
    for who, expected in ((person("reader", ["product"]), ["jira_search"]),
                          (person("lead", ["product", "leads"]), ["jira_search", "jira_create_issue"]),
                          (person("lead-ro", ["product", "leads"], write=False), ["jira_search"])):
        gw.upstream.replies.append(completion("ok"))
        r = await gw.client.post("/v1/chat/completions", headers=gw.auth(who, **headers), json=chat)
        assert r.status_code == 200 and _tool_names(gw) == expected


async def test_a_paused_approval_is_checked_again_for_the_current_caller(make_gateway):
    gw = await _gateway(make_gateway, agents())
    headers = {"X-Env-JIRA_USER": "alice"}
    lead = person("lead", ["product", "leads"])
    msgs = [{"role": "user", "content": "make it"}]
    gw.upstream.replies.append(completion(calls=[("jira_create_issue", {"summary": "Fix"})]))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(lead, **headers), json={"model": "writer", "messages": msgs})
    text = r.json()["choices"][0]["message"]["content"]
    code = re.search(r"approve (\w+)`", text).group(1)
    history = [*msgs, {"role": "assistant", "content": text}, {"role": "user", "content": f"approve {code}"}]
    # the same person, demoted to a reader before answering
    PEOPLE["lead"] = Principal(user_id="lead", username="lead", groups=("product",))
    r = await gw.client.post("/v1/chat/completions", headers=gw.auth(lead, **headers), json={"model": "writer", "messages": history})
    assert "no longer run" in r.json()["choices"][0]["message"]["content"]
    assert gw.tools.calls == []
