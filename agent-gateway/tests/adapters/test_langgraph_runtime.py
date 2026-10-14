"""The LangGraph driver must behave exactly like the reference driver."""

import re

import pytest

from agent_gateway.adapters.outbound.langgraph_runtime.graph import LangGraphDriver
from agent_gateway.domain.errors import LoopExhausted
from agent_gateway.domain.messages import ChatMessage
from tests.application.fakes import agent, call, client_tool, rig, say, spec, user

DRIVER = LangGraphDriver()


def with_graph(r):
    r.driver = DRIVER
    return r


async def test_a_plain_answer():
    r = with_graph(rig(say("Hello from the graph"), ))
    await r.turn([user("hi")])
    assert r.sink.content == "Hello from the graph" and r.sink.done.usage.total_tokens == 15


async def test_tool_loop_and_correction():
    r = with_graph(rig(call(("nope", {})), call(("t", {})), say("done"), specs={"kb": [spec("t", "kb")]},
                       agents=[agent(tools=[{"server": "kb"}])]))
    await r.turn([user("go")])
    assert r.sink.content == "done" and [c[1] for c in r.tools.calls] == ["t"]


async def test_approval_round_trip_through_the_graph():
    specs = {"skills": [spec("jira_create_issue", summary={"type": "string"})]}
    ag = agent(tools=[{"server": "skills", "approval": ["jira_create_issue"]}])
    r = with_graph(rig(call(("jira_create_issue", {"summary": "S"}, "w1")), say("created"), specs=specs, agents=[ag]))
    msgs = [user("make it")]
    await r.turn(msgs, request_vars={"skills": {"JIRA_USER": "a"}})
    code = re.search(r"approve (\w+)`", r.sink.content).group(1)
    assert r.tools.calls == []
    await r.turn([*msgs, r.visible_reply(), user(f"approve {code}")], request_vars={"skills": {"JIRA_USER": "a"}})
    assert [c[1] for c in r.tools.calls] == ["jira_create_issue"] and r.sink.content == "created"


async def test_loop_limit_surfaces_as_an_error():
    r = with_graph(rig(*[call(("nope", {})) for _ in range(12)]))
    with pytest.raises(LoopExhausted):
        await r.turn([user("x")])


async def test_client_tool_round_trip():
    r = with_graph(rig(call(("Read", {}, "c1")), say("read it")))
    tools = [client_tool("Read")]
    msgs = [user("read")]
    await r.turn(msgs, client_tools=tools)
    assert r.sink.done.finish_reason == "tool_calls"
    await r.turn([*msgs, r.visible_reply(), ChatMessage(role="tool", tool_call_id="c1", content="x")], client_tools=tools)
    assert r.sink.content == "read it"
