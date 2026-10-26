# Covers: AS-01, AS-02, AS-03, AS-04, AS-05, FR-ACT-04, FR-CRD-04, FR-CFG-03

"""The acceptance scenarios, end to end over HTTP with a scripted upstream."""

import json
import logging

import pytest

from agent_gateway.adapters.inbound.openai_http import chat
from agent_gateway.application.models import ToolResult, ToolSpec
from tests.application.fakes import agent, spec
from tests.conftest import completion

JIRA = {"skills": [spec("jira_search", query={"type": "string"}),
                   spec("jira_create_issue", summary={"type": "string"})]}
TOOLS = [{"server": "skills", "include": ["jira_*"], "approval": ["jira_create_issue"]}]
CREDS = {"X-Env-JIRA_USER": "alice-secret-name"}
ASK = {"type": "function", "function": {"name": "AskUserQuestion", "parameters": {"type": "object", "properties": {}}}}


def post(gw, messages, **extra):
    return gw.client.post("/v1/chat/completions", headers=gw.auth(**CREDS),
                          json={"model": "router", "messages": messages, **extra})


async def test_as01_as02_an_agent_streams_and_a_plain_model_is_untouched(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies += [completion("agent says hi"), completion("plain")]
    r = await post(gw, [{"role": "user", "content": "hi"}], stream=True)
    assert "agent says hi" in "".join(json.loads(e[6:])["choices"][0]["delta"].get("content", "")
                                      for e in r.text.split("\n\n") if e.startswith("data: {") and json.loads(e[6:])["choices"])
    payload = {"model": "some-model", "messages": [{"role": "user", "content": "hi"}]}
    await gw.client.post("/v1/chat/completions", headers=gw.auth(), json=payload)
    assert json.loads(gw.upstream.seen[-1][3]) == payload


async def test_as03_rejecting_a_write_through_the_clients_ask_tool_writes_nothing(make_gateway):
    gw = await make_gateway([agent("router", tools=TOOLS)], specs=JIRA)
    gw.upstream.replies += [completion(calls=[("jira_create_issue", {"summary": "S"})]), completion("Cancelled.")]
    msgs = [{"role": "user", "content": "make a ticket"}]
    first = (await post(gw, msgs, tools=[ASK])).json()["choices"][0]["message"]
    ask = first["tool_calls"][0]
    assert ask["function"]["name"] == "AskUserQuestion" and gw.tools.calls == []
    second = await post(gw, [*msgs, first, {"role": "tool", "tool_call_id": ask["id"], "content": "Reject"}], tools=[ASK])
    assert second.json()["choices"][0]["message"]["content"] == "Cancelled." and gw.tools.calls == []


async def test_as03_approving_runs_exactly_the_previewed_write(make_gateway):
    gw = await make_gateway([agent("router", tools=TOOLS)], specs=JIRA)
    gw.upstream.replies += [completion(calls=[("jira_create_issue", {"summary": "S"})]), completion("Created.")]
    msgs = [{"role": "user", "content": "make a ticket"}]
    first = (await post(gw, msgs, tools=[ASK])).json()["choices"][0]["message"]
    ask = first["tool_calls"][0]
    await post(gw, [*msgs, first, {"role": "tool", "tool_call_id": ask["id"], "content": "Approve"}], tools=[ASK])
    assert [(c[1], c[2]) for c in gw.tools.calls] == [("jira_create_issue", {"summary": "S", "confirm": True})]


async def test_as04_a_follow_up_after_a_handoff_is_answered_by_the_specialist(make_gateway):
    router = agent("router", handoffs=[{"to": "tasks", "description": "tickets"}])
    gw = await make_gateway([router, agent("tasks", model="tasks-model")])
    gw.upstream.replies += [completion(calls=[("transfer_to_tasks", {})]), completion("Tasks here."), completion("Still tasks.")]
    msgs = [{"role": "user", "content": "I need a ticket"}]
    first = (await post(gw, msgs)).json()
    assert first["choices"][0]["message"]["content"] == "Tasks here."
    assert "Handing off from router to tasks" in first["choices"][0]["message"]["reasoning_content"]
    await post(gw, [*msgs, first["choices"][0]["message"], {"role": "user", "content": "and a second"}])
    assert json.loads(gw.upstream.seen[-1][3])["model"] == "tasks-model"


async def test_as05_a_model_that_calls_an_unknown_tool_still_yields_one_correct_answer(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies += [completion(calls=[("does_not_exist", {})]), completion("The real answer.")]
    r = await post(gw, [{"role": "user", "content": "hi"}])
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "The real answer."
    retry = json.loads(gw.upstream.seen[-1][3])["messages"]
    assert any(m["role"] == "tool" and "no tool named 'does_not_exist'" in m["content"] for m in retry)


async def test_credentials_never_reach_the_model_the_response_or_the_logs(make_gateway, caplog):
    gw = await make_gateway([agent("router", tools=TOOLS)], specs=JIRA)

    async def leaky(server, tool, arguments, credentials, *, identity=None):
        gw.tools.calls.append((server, tool, arguments, credentials))
        return ToolResult(f"echo: {credentials['JIRA_USER']}")

    gw.tools.call = leaky
    gw.upstream.replies += [completion(calls=[("jira_search", {"query": "x"})]), completion("done")]
    with caplog.at_level(logging.DEBUG):
        r = await post(gw, [{"role": "user", "content": "find"}])
    assert gw.tools.calls[0][3] == {"JIRA_USER": "alice-secret-name"}
    sent = b"".join(c[3] for c in gw.upstream.seen).decode()
    assert "alice-secret-name" not in sent and "alice-secret-name" not in r.text
    assert "alice-secret-name" not in caplog.text


async def test_slow_gateway_tools_keep_the_stream_alive(make_gateway, monkeypatch):
    import asyncio

    monkeypatch.setattr(chat, "KEEPALIVE_SECONDS", 0.05)
    gw = await make_gateway([agent("router", tools=TOOLS)], specs=JIRA)

    async def slow(server, tool, arguments, credentials, *, identity=None):
        await asyncio.sleep(0.3)
        return ToolResult("found")

    gw.tools.call = slow
    gw.upstream.replies += [completion("Looking.", calls=[("jira_search", {"query": "x"})]), completion("Found it.")]
    r = await post(gw, [{"role": "user", "content": "find"}], stream=True)
    assert ": keep-alive" in r.text and r.text.rstrip().endswith("[DONE]")


def test_secrets_come_only_from_the_environment(tmp_path):
    from agent_gateway.adapters.outbound.config_files import load_config
    from agent_gateway.config import EnvSettings
    from agent_gateway.domain.errors import ConfigError

    (tmp_path / "agents").mkdir()
    (tmp_path / "gateway.yaml").write_text("upstream_api_key: sk-nope\n")
    with pytest.raises(ConfigError, match="upstream_api_key"):
        load_config(tmp_path, {})
    (tmp_path / "gateway.yaml").write_text("limits: {max_steps: 3}\n")
    (tmp_path / "mcp-servers.yaml").write_text("servers: {s: {url: http://x, password: p}}\n")
    with pytest.raises(ConfigError, match="password"):
        load_config(tmp_path, {})
    with pytest.raises(ConfigError, match="CREDS_KEY"):
        EnvSettings.from_env({"UPSTREAM_BASE_URL": "http://u", "DATABASE_URL": "x", "REDIS_URL": "y", "APPROVAL_SECRET": "z"})
