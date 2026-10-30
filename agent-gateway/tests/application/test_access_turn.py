# Covers: FR-ENT-05, FR-ENT-06, FR-ENT-07, FR-ENT-09

import re

from agent_gateway.domain.identity import KeyScope, Principal
from tests.application.fakes import agent, call, rig, say, spec, user

JIRA = {"skills": [spec("jira_search", query={"type": "string"}),
                   spec("jira_create_issue", summary={"type": "string"}, confirm={"type": "boolean"})]}
TOOLS = [{"server": "skills", "include": ["jira_*"], "approval": ["jira_create_issue"]}]
HEADER = {"skills": {"JIRA_USER": "alice"}}


def who(groups=(), write=True):
    return Principal(user_id="u1", username="u1", groups=tuple(groups), scope=KeyScope(write=write))


def writer_agent(**access):
    return agent("router", tools=TOOLS, access=access or None)


def offered(r, i=0):
    return [d.function.name for d in r.llm.requests[i].tools]


async def test_write_tools_are_hidden_from_readers_and_shown_to_writers():
    access = {"groups": ["product"], "write_groups": ["leads"]}
    reader = rig(say("ok"), specs=JIRA, agents=[writer_agent(**access)])
    await reader.turn([user("hi")], request_vars=HEADER, principal=who(["product"]))
    assert offered(reader) == ["jira_search"]
    lead = rig(say("ok"), specs=JIRA, agents=[writer_agent(**access)])
    await lead.turn([user("hi")], request_vars=HEADER, principal=who(["product", "leads"]))
    assert offered(lead) == ["jira_search", "jira_create_issue"]


async def test_a_read_only_key_never_gets_write_tools_whatever_the_groups():
    r = rig(say("ok"), specs=JIRA, agents=[writer_agent(groups=["product"], write_groups=["product"])])
    await r.turn([user("hi")], request_vars=HEADER, principal=who(["product"], write=False))
    assert offered(r) == ["jira_search"]


async def test_a_model_calling_a_hidden_write_tool_anyway_runs_nothing():
    r = rig(call(("jira_create_issue", {"summary": "x"})), say("sorry"), specs=JIRA,
            agents=[writer_agent(write_groups=["leads"])])
    await r.turn([user("hi")], request_vars=HEADER, principal=who())
    assert r.tools.calls == [] and r.sink.content.endswith("sorry")


async def test_handoffs_to_agents_the_caller_may_not_use_are_not_offered():
    router = agent("router", handoffs=[{"to": "tasks", "description": "t"}, {"to": "secret", "description": "s"}])
    agents = [router, agent("tasks"), agent("secret", access={"groups": ["admins"]})]
    r = rig(say("a"), say("b"), agents=agents)
    await r.turn([user("hi")], principal=who())
    assert offered(r) == ["transfer_to_tasks"]
    r2 = rig(say("a"), agents=agents)
    await r2.turn([user("hi")], principal=who(["admins"]))
    assert offered(r2) == ["transfer_to_tasks", "transfer_to_secret"]


async def test_a_thread_does_not_continue_with_a_specialist_the_caller_lost():
    router = agent("router", handoffs=[{"to": "tasks", "description": "t"}])
    tasks = agent("tasks", model="tasks-model", access={"groups": ["g"]})
    r = rig(call(("transfer_to_tasks", {})), say("tasks here"), say("router again"), agents=[router, tasks])
    msgs = [user("go")]
    await r.turn(msgs, principal=who(["g"]))
    await r.turn([*msgs, r.visible_reply(), user("more")], principal=who())     # left the group meanwhile
    assert r.llm.requests[2].model == "base-model" and r.sink.content == "router again"


def _code(r):
    return re.search(r"approve (\w+)`", r.sink.content).group(1)


async def _paused(agents, principal):
    r = rig(call(("jira_create_issue", {"summary": "Fix"}, "w1")), say("created"), specs=JIRA, agents=agents)
    msgs = [user("make it")]
    await r.turn(msgs, request_vars=HEADER, principal=principal)
    return r, msgs, _code(r), r.visible_reply()


async def test_resuming_an_approval_runs_it_for_a_caller_who_may_still_write():
    r, msgs, code, asst = await _paused([writer_agent(write_groups=["leads"])], who(["leads"]))
    await r.turn([*msgs, asst, user(f"approve {code}")], request_vars=HEADER, principal=who(["leads"]))
    assert [c[1] for c in r.tools.calls] == ["jira_create_issue"]


async def test_resuming_an_approval_after_losing_write_runs_nothing():
    r, msgs, code, asst = await _paused([writer_agent(write_groups=["leads"])], who(["leads"]))
    await r.turn([*msgs, asst, user(f"approve {code}")], request_vars=HEADER, principal=who())
    assert r.tools.calls == [] and "no longer run `jira_create_issue`" in r.sink.content


async def test_resuming_after_losing_the_agent_runs_nothing():
    r, msgs, code, asst = await _paused([writer_agent(groups=["product"])], who(["product"]))
    await r.turn([*msgs, asst, user(f"approve {code}")], request_vars=HEADER, principal=who())
    assert r.tools.calls == [] and "no longer use the agent" in r.sink.content
