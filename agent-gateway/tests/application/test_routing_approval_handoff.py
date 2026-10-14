# Covers: FR-TLR-07, FR-TLR-08, FR-TLR-09, FR-TLR-01, FR-TLR-04, FR-TLR-05, FR-TLR-06, FR-APR-01, FR-APR-02, FR-APR-03, FR-APR-04, FR-APR-05, FR-APR-06, FR-APR-07, FR-HND-01, FR-HND-02, FR-HND-03

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


async def test_the_agents_own_tool_wins_over_a_clients_equivalent_when_the_gateway_can_run_it():
    r = rig(call(("jira_search", {"query": "x"})), say("found it"), specs=JIRA, agents=[jira_agent()])
    tools = [client_tool("mcp__j__jira_search", query={"type": "string"}), client_tool("Read")]
    await r.turn([user("find x")], client_tools=tools, request_vars=HEADER)
    offered = [d.function.name for d in r.llm.requests[0].tools]
    assert "jira_search" in offered and "mcp__j__jira_search" not in offered and "Read" in offered
    assert [c[1] for c in r.tools.calls] == ["jira_search"] and r.sink.content == "found it"


async def test_a_clients_equivalent_is_withheld_until_the_user_opts_in_and_then_stays_for_the_conversation():
    r = rig(say("Your Jira credentials are missing. Want to use your client's own Jira tools instead?"),
            call(("use_client_tools", {}, "u1")), call(("mcp__j__jira_search", {"query": "x"}, "c1")), say("done"),
            call(("mcp__j__jira_search", {"query": "y"}, "c2")),
            specs=JIRA, agents=[jira_agent()])
    tools = [client_tool("mcp__j__jira_search", query={"type": "string"})]
    msgs = [user("find x")]
    await r.turn(msgs, client_tools=tools)                                       # no credentials anywhere
    offered = {d.function.name for d in r.llm.requests[0].tools}
    assert "mcp__j__jira_search" not in offered and "jira_search" not in offered and "use_client_tools" in offered
    note = " ".join(m.text() for m in r.llm.requests[0].messages if m.role == "system")
    assert "ask whether" in note and "on your own initiative" in note
    assert r.sink.tool_calls == []                                               # it asked; nothing ran
    # the user says yes: the model opts in, then the client's equivalent is offered and used
    msgs = [*msgs, r.visible_reply(), user("yes, use my client's tools")]
    await r.turn(msgs, client_tools=tools)
    assert "mcp__j__jira_search" in {d.function.name for d in r.llm.requests[2].tools}
    assert [c.function.name for c in r.sink.tool_calls] == ["mcp__j__jira_search"] and r.tools.calls == []
    msgs = [*msgs, r.visible_reply(), ChatMessage(role="tool", tool_call_id="c1", content="PAY-1")]
    await r.turn(msgs, client_tools=tools)
    assert r.sink.content == "done"
    # still opted in on the next turn of the same conversation
    msgs = [*msgs, r.visible_reply(), user("and y?")]
    await r.turn(msgs, client_tools=tools)
    assert [c.function.name for c in r.sink.tool_calls] == ["mcp__j__jira_search"]
    # a new conversation starts without the opt-in
    r2 = rig(say("hi"), specs=JIRA, agents=[jira_agent()])
    r2.store = r.store
    r2.deps.store = r.store
    await r2.turn([user("fresh start")], client_tools=tools)
    assert "mcp__j__jira_search" not in {d.function.name for d in r2.llm.requests[0].tools}


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
    r = rig(say("Your Jira credentials are missing."), specs=JIRA, agents=[jira_agent()], creds=creds)
    await r.turn([user("q")], user="u2")
    assert r.tools.calls == []                      # u2 has nothing, so u1's credentials are never used
    assert "jira_search" not in [d.function.name for d in r.llm.requests[0].tools]


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
    r = rig(call(("use_client_tools", {}, "u1")),
            call(("mcp__j__jira_create_issue", {"summary": "Fix", "confirm": True}, "w1")), say("done"),
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


async def test_tools_nobody_can_run_are_hidden_and_the_model_is_told_why():
    # Regression: with no credentials and no client equivalent, calls were handed to a client that
    # did not have the tool ("Tool 'jira_my_work' does not exist"), and the model went hunting.
    specs = {**JIRA, "kb": [spec("kb_search", "kb")]}
    ag = agent(tools=[*TOOLS, {"server": "kb"}])
    r = rig(say("I can't reach Jira without your credentials."), specs=specs, agents=[ag])
    await r.turn([user("my tickets")], client_tools=[client_tool("terminal"), client_tool("clarify")])
    request = r.llm.requests[0]
    offered = {d.function.name for d in request.tools}
    assert offered == {"kb_search", "terminal", "clarify", "use_client_tools"}   # credential-free tools stay
    note = " ".join(m.text() for m in request.messages if m.role == "system")
    assert "unavailable" in note and "jira_search" in note and "skills" in note and "use_client_tools" in note
    assert r.tools.calls == [] and r.sink.tool_calls == []


async def test_a_hidden_tool_called_anyway_gets_a_correction_not_a_client_call():
    r = rig(call(("jira_search", {"query": "x"})), say("ok"), specs=JIRA, agents=[jira_agent()])
    await r.turn([user("q")])
    assert r.sink.tool_calls == [] and r.tools.calls == [] and r.sink.content == "ok"


async def test_a_client_equivalent_is_not_offered_in_place_of_the_agents_tool_without_the_opt_in():
    r = rig(say("x"), specs=JIRA, agents=[jira_agent()])
    await r.turn([user("q")], client_tools=[client_tool("mcp__j__jira_search"), client_tool("Read")])
    offered = {d.function.name for d in r.llm.requests[0].tools}
    assert "mcp__j__jira_search" not in offered and "jira_create_issue" not in offered and "Read" in offered


async def test_connector_style_client_tools_are_held_back_by_server_terms_until_the_user_opts_in():
    # Regression: A client's Jira connector has a different name from the agent's jira_* tools, so it was
    # not recognized as a stand-in and the model used it without asking.
    from agent_gateway.application.models import ServerInfo

    servers = {"skills": ServerInfo("skills", credentials=("JIRA_USER",), client_terms=("jira", "confluence")),
               "kb": ServerInfo("kb")}
    tools = [client_tool("connectors__jira__SEARCH_ISSUES"), client_tool("mcp__atlassian__x_confluence_page"),
             client_tool("terminal"), client_tool("read_file"), client_tool("web_search")]
    r = rig(say("asked"), call(("use_client_tools", {}, "u1")), say("ok"), specs=JIRA, agents=[jira_agent()], servers=servers)
    msgs = [user("my tickets")]
    await r.turn(msgs, client_tools=tools)
    offered = {d.function.name for d in r.llm.requests[0].tools}
    assert offered == {"terminal", "read_file", "web_search", "use_client_tools"}
    msgs = [*msgs, r.visible_reply(), user("yes, use the connector")]
    await r.turn(msgs, client_tools=tools)
    offered = {d.function.name for d in r.llm.requests[2].tools}
    assert {"connectors__jira__SEARCH_ISSUES", "mcp__atlassian__x_confluence_page"} <= offered



async def test_the_receiving_agent_is_told_it_now_holds_the_conversation():
    # Regression: after a handoff the specialist replied "wait for Plan's output" as if it were the router.
    router = agent("router", handoffs=[{"to": "plan", "description": "ideas"}])
    r = rig(call(("transfer_to_plan", {})), say("Plan here."), agents=[router, agent("plan", name="Plan")])
    await r.turn([user("shape an idea")])
    note = [m.text() for m in r.llm.requests[1].messages if m.role == "tool"][-1]
    assert "you are now Plan" in note and "yourself" in note


async def test_a_question_through_clarify_carries_the_models_options_not_approve_reject():
    import json as _json

    r = rig(call(("ask_user", {"question": "Which ticket?", "options": ["PAY-1", "PAY-2"]}, "q1")),
            agents=[agent(interaction=["ask_user"])])
    await r.turn([user("log time")], client_tools=[client_tool("clarify")])
    args = _json.loads(r.sink.tool_calls[0].function.arguments)
    assert r.sink.tool_calls[0].function.name == "clarify"
    assert args == {"questions": [{"question": "Which ticket?", "choices": ["PAY-1", "PAY-2"]}]}


async def test_an_approval_answered_through_clarify_runs_the_write():
    r = rig(call(("jira_create_issue", {"summary": "S"}, "w1")), say("created"), specs=JIRA, agents=[jira_agent()])
    msgs = [user("create")]
    await r.turn(msgs, request_vars=HEADER, client_tools=[client_tool("clarify")])
    ask = r.sink.tool_calls[0]
    answer = '{"responses": [{"question": "Run it?", "choices_offered": ["Approve", "Reject"], "status": "answered", "user_response": "Approve"}], "outcome": "submitted"}'
    await r.turn(followup(msgs, r.visible_reply(), ChatMessage(role="tool", tool_call_id=ask.id, content=answer)),
                 request_vars=HEADER, client_tools=[client_tool("clarify")])
    assert [c[1] for c in r.tools.calls] == ["jira_create_issue"]
