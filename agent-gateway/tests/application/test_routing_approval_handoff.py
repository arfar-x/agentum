# Covers: FR-TLR-01, FR-TLR-04, FR-TLR-05, FR-TLR-06, FR-APR-01, FR-APR-02, FR-APR-03, FR-APR-04, FR-APR-05, FR-APR-06, FR-APR-07, FR-HND-01, FR-HND-02, FR-HND-03

import re

from agent_gateway.domain.messages import ChatMessage
from tests.application.fakes import FakeCreds, agent, call, client_tool, rig, say, spec, user

JIRA = {"skills": [spec("jira_search", query={"type": "string"}),
                   spec("jira_create_issue", summary={"type": "string"}, confirm={"type": "boolean"})]}
TOOLS = [{"server": "skills", "include": ["jira_*"], "approval": ["jira_create_issue"]}]
HEADER = {"skills": {"JIRA_USER": "alice"}}


def jira_agent(**kw):
    return agent(tools=TOOLS, **kw)


def followup(history, reply, *extra):
    return [*history, reply, *extra]


async def test_the_users_own_client_runs_the_tool_when_it_has_an_equivalent():
    r = rig(call(("mcp__j__jira_search", {"query": "x"}), text="Looking."), say("done"),
            specs=JIRA, agents=[jira_agent()])
    tools = [client_tool("mcp__j__jira_search", query={"type": "string"})]
    msgs = [user("find x")]
    await r.turn(msgs, client_tools=tools, request_vars=HEADER)
    out = r.sink.tool_calls
    assert [c.function.name for c in out] == ["mcp__j__jira_search"]
    assert r.tools.calls == [] and r.sink.done.finish_reason == "tool_calls"
    offered = [d.function.name for d in r.llm.requests[0].tools]
    assert "mcp__j__jira_search" in offered and "jira_search" not in offered   # the client's version replaces ours
    asst = r.visible_reply()
    await r.turn(followup(msgs, asst, ChatMessage(role="tool", tool_call_id=out[0].id, content="PAY-1")),
                 client_tools=tools)
    assert r.sink.content == "done"


async def test_without_client_tool_request_credentials_run_in_the_gateway():
    r = rig(call(("jira_search", {"query": "x"})), say("found it"), specs=JIRA, agents=[jira_agent()])
    await r.turn([user("find x")], request_vars=HEADER)
    assert r.tools.calls[0][1:] == ("jira_search", {"query": "x"}, {"JIRA_USER": "alice"})
    assert r.sink.content == "found it"


async def test_stored_credentials_are_used_when_the_request_has_none_and_only_for_their_owner():
    creds = FakeCreds({("u1", "skills"): {"JIRA_USER": "stored-u1"}})
    r = rig(call(("jira_search", {"query": "x"})), say("ok"), specs=JIRA, agents=[jira_agent()], creds=creds)
    await r.turn([user("q")], user="u1")
    assert r.tools.calls[0][3] == {"JIRA_USER": "stored-u1"}
    r = rig(call(("jira_search", {"query": "x"})), specs=JIRA, agents=[jira_agent()], creds=creds)
    await r.turn([user("q")], user="u2")
    assert r.tools.calls == []                      # u2 has nothing: the call goes to the client
    assert [c.function.name for c in r.sink.tool_calls] == ["jira_search"]


async def test_request_credentials_beat_stored_ones():
    creds = FakeCreds({("u1", "skills"): {"JIRA_USER": "stored"}})
    r = rig(call(("jira_search", {"query": "x"})), say("ok"), specs=JIRA, agents=[jira_agent()], creds=creds)
    await r.turn([user("q")], request_vars=HEADER)
    assert r.tools.calls[0][3] == {"JIRA_USER": "alice"}


async def test_a_mixed_step_keeps_the_gateway_results_across_the_client_call():
    specs = {"kb": [spec("kb_search", "kb")], **JIRA}
    ag = agent(tools=[{"server": "kb"}])
    r = rig(call(("kb_search", {}, "k1"), ("Read", {}, "c1")), say("both done"), specs=specs, agents=[ag])
    tools = [client_tool("Read")]
    msgs = [user("go")]
    await r.turn(msgs, client_tools=tools)
    out = r.sink.tool_calls
    assert [c.id for c in out] == ["c1"]
    asst = r.visible_reply()
    await r.turn(followup(msgs, asst, ChatMessage(role="tool", tool_call_id="c1", content="file text")), client_tools=tools)
    sent = r.llm.requests[1].messages
    roles = [(m.role, m.tool_call_id) for m in sent if m.role == "tool"]
    assert ("tool", "k1") in roles and ("tool", "c1") in roles
    assert [m for m in sent if m.role == "assistant"][0].tool_calls and len([m for m in sent if m.role == "assistant"][0].tool_calls) == 2


# ---- approval through the client's own ask tool

ASK = [client_tool("AskUserQuestion")]


async def _until_approval(r, ask=True):
    msgs = [user("create a ticket")]
    await r.turn(msgs, request_vars=HEADER, client_tools=ASK if ask else [])
    return msgs


async def test_a_write_is_previewed_through_the_clients_ask_tool_and_nothing_runs():
    r = rig(call(("jira_create_issue", {"summary": "Fix", "confirm": True}, "w1")), say("created"),
            specs=JIRA, agents=[jira_agent()])
    await _until_approval(r)
    assert r.tools.calls == []
    ask = r.sink.tool_calls[0]
    assert ask.function.name == "AskUserQuestion"
    assert "jira_create_issue" in ask.function.arguments and "Fix" in ask.function.arguments


async def test_approving_runs_exactly_the_previewed_call_and_the_model_cannot_pre_confirm():
    r = rig(call(("jira_create_issue", {"summary": "Fix", "confirm": True}, "w1")), say("created PAY-9"),
            specs=JIRA, agents=[jira_agent()])
    msgs = await _until_approval(r)
    asst = r.visible_reply()
    ask_id = asst.tool_calls[0].id
    await r.turn(followup(msgs, asst, ChatMessage(role="tool", tool_call_id=ask_id, content="Approve")),
                 request_vars=HEADER, client_tools=ASK)
    assert len(r.tools.calls) == 1
    _, tool, args, _ = r.tools.calls[0]
    assert tool == "jira_create_issue" and args == {"summary": "Fix", "confirm": True}   # gateway-added
    assert r.sink.content == "created PAY-9"


async def test_rejecting_runs_nothing_and_tells_the_model():
    r = rig(call(("jira_create_issue", {"summary": "Fix"}, "w1")), say("ok, cancelled"),
            specs=JIRA, agents=[jira_agent()])
    msgs = await _until_approval(r)
    asst = r.visible_reply()
    await r.turn(followup(msgs, asst, ChatMessage(role="tool", tool_call_id=asst.tool_calls[0].id, content="Reject")),
                 request_vars=HEADER, client_tools=ASK)
    assert r.tools.calls == []
    assert any("declined" in m.text() for m in r.llm.requests[1].messages if m.role == "tool")


async def test_an_unclear_answer_is_treated_as_a_rejection():
    r = rig(call(("jira_create_issue", {"summary": "Fix"}, "w1")), say("hm"), specs=JIRA, agents=[jira_agent()])
    msgs = await _until_approval(r)
    asst = r.visible_reply()
    await r.turn(followup(msgs, asst, ChatMessage(role="tool", tool_call_id=asst.tool_calls[0].id, content="maybe later")),
                 request_vars=HEADER, client_tools=ASK)
    assert r.tools.calls == []


# ---- approval through the text fallback

def _code(r):
    return re.search(r"approve (\w+)`", r.sink.content).group(1)


async def test_without_an_ask_tool_the_approval_is_text_with_a_code():
    r = rig(call(("jira_create_issue", {"summary": "Fix"}, "w1")), say("created"), specs=JIRA, agents=[jira_agent()])
    msgs = await _until_approval(r, ask=False)
    assert "jira_create_issue" in r.sink.content and "approve " in r.sink.content
    code, asst = _code(r), r.visible_reply()
    await r.turn(followup(msgs, asst, user(f"approve {code}")), request_vars=HEADER)
    assert [c[1] for c in r.tools.calls] == ["jira_create_issue"]
    assert r.sink.content == "created"


async def test_a_reused_wrong_foreign_or_expired_code_changes_nothing():
    def fresh():
        return rig(call(("jira_create_issue", {"summary": "Fix"}, "w1")), say("created"), specs=JIRA, agents=[jira_agent()])

    r = fresh()
    msgs = await _until_approval(r, ask=False)
    code, asst = _code(r), r.visible_reply()
    history = followup(msgs, asst, user(f"approve {code}"))
    await r.turn(history, request_vars=HEADER)                       # first use runs it
    assert len(r.tools.calls) == 1
    await r.turn(history, request_vars=HEADER)                       # reuse
    assert "not valid or has expired" in r.sink.content and len(r.tools.calls) == 1
    for who, text, tick in (("u1", "approve wrongcode", 0), ("u2", f"approve {code}", 0), ("u1", f"approve {code}", 10_000)):
        r = fresh()
        msgs = await _until_approval(r, ask=False)
        code, asst = _code(r), r.visible_reply()
        text = text.replace("{code}", code) if "{code}" in text else (text if "wrong" in text else f"approve {code}")
        r.clock.t += tick
        await r.turn(followup(msgs, asst, user(text)), user=who, request_vars=HEADER)
        assert r.tools.calls == [] and "not valid or has expired" in r.sink.content


async def test_a_write_the_client_runs_itself_is_left_to_the_clients_own_prompt():
    r = rig(call(("mcp__j__jira_create_issue", {"summary": "Fix", "confirm": True}, "w1")), say("done"),
            specs=JIRA, agents=[jira_agent()])
    await r.turn([user("create")], client_tools=[client_tool("mcp__j__jira_create_issue")])
    assert r.tools.calls == []
    assert r.sink.tool_calls[0].function.name == "mcp__j__jira_create_issue"
    assert '"confirm": true' in r.sink.tool_calls[0].function.arguments


# ---- handoffs

async def test_a_handoff_switches_agent_and_later_turns_stay_with_the_specialist():
    router = agent("router", handoffs=[{"to": "tasks", "description": "Jira work"}])
    tasks = agent("tasks", instructions="You are Tasks.", model="tasks-model")
    r = rig(call(("transfer_to_tasks", {}), text="Passing you to Tasks."), say("Tasks here."), say("Tasks again."),
            agents=[router, tasks])
    msgs = [user("make a ticket")]
    await r.turn(msgs)
    assert "Handing off from router to tasks" in r.sink.reasoning
    assert r.llm.requests[1].model == "tasks-model" and "You are Tasks." in r.llm.requests[1].messages[0].text()
    assert "Passing you to Tasks." in r.sink.content and "Tasks here." in r.sink.content
    asst = r.visible_reply()
    await r.turn(followup(msgs, asst, user("and another")), agent="router")
    assert r.llm.requests[2].model == "tasks-model"


async def test_an_edited_history_starts_a_fresh_thread_without_error():
    router = agent("router", handoffs=[{"to": "tasks", "description": "x"}])
    r = rig(call(("transfer_to_tasks", {})), say("one"), say("fresh"), agents=[router, agent("tasks", model="tm")])
    await r.turn([user("a")])
    await r.turn([user("a"), ChatMessage(role="assistant", content="something the client rewrote"), user("b")])
    assert r.llm.requests[2].model == "base-model"
    assert r.sink.content == "fresh"
