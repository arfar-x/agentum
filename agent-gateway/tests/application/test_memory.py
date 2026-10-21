# Covers: FR-MEM-01, FR-MEM-02, FR-MEM-03, FR-MEM-05, FR-MEM-06

from agent_gateway.application.models import ToolResult
from agent_gateway.domain.messages import ChatMessage
from tests.application.fakes import agent, call, client_tool, rig, say, spec, user

KB = {"kb": {"enabled": True}}


def system_text(request) -> str:
    return " ".join(m.text() for m in request.messages if m.role == "system")


def kb_rig(*script, **kw):
    return rig(*script, specs={"kb": [spec("kb_search", "kb")]}, agents=[agent(tools=[{"server": "kb"}])], **kw)


async def test_the_next_turn_is_told_what_the_gateway_ran_before():
    r = kb_rig(call(("kb_search", {"queries": ["cto"]})), say("The CTO is X."), say("I looked it up."))
    await r.turn([user("who is the cto")])
    assert "Earlier in this conversation" not in system_text(r.llm.requests[0])
    await r.turn([user("who is the cto"), r.visible_reply(), user("how do you know?")])
    note = system_text(r.llm.requests[2])
    assert "Earlier in this conversation" in note and 'kb_search {"queries": ["cto"]} -> worked' in note
    assert "Do not say you guessed" in note


async def test_a_failed_call_is_recorded_as_failed_and_results_are_never_stored():
    r = kb_rig(call(("kb_search", {"queries": ["cto"]})), say("Could not search."), say("ok"),
               results={})
    async def failing(server, tool, arguments, credentials):
        return ToolResult("SECRET-RESULT-TEXT", is_error=True)
    r.tools.call = failing
    await r.turn([user("who")])
    thread = next(iter(r.store.threads.values()))
    assert thread.trace[0]["ok"] is False
    assert "SECRET-RESULT-TEXT" not in str(thread.trace)


async def test_a_catalog_lookup_before_the_answer_is_recorded():
    class Retriever:
        async def search(self, queries, limit, types):
            return [{"title": {"en": "a"}, "summary": {"en": "b"}, "type": "doc", "id": "form:cto"}]

    r = rig(say("answer"), say("later"), agents=[agent(preretrieval=KB)], retriever=Retriever())
    await r.turn([user("who is the cto")])
    thread = next(iter(r.store.threads.values()))
    assert thread.trace[0]["tool"] == "catalog lookup" and thread.trace[0]["found"] == 1
    assert thread.trace[0]["args"] == {"query": "who is the cto"}


async def test_client_run_calls_are_not_recorded():
    r = rig(call(("local_tool", {"x": 1})), agents=[agent()])
    await r.turn([user("go")], client_tools=[client_tool("local_tool", x={"type": "integer"})])
    thread = next(iter(r.store.threads.values()))
    assert thread.trace == []


async def test_the_record_carries_across_several_turns():
    r = kb_rig(call(("kb_search", {"queries": ["one"]})), say("A"),
               call(("kb_search", {"queries": ["two"]})), say("B"), say("C"))
    h1 = [user("q1")]
    await r.turn(h1)
    h2 = [*h1, r.visible_reply(), user("q2")]
    await r.turn(h2)
    h3 = [*h2, r.visible_reply(), user("q3")]
    await r.turn(h3)
    note = system_text(r.llm.requests[-1])
    assert '"one"' in note and '"two"' in note


async def test_a_history_that_cannot_be_matched_starts_without_a_note():
    r = kb_rig(call(("kb_search", {"queries": ["cto"]})), say("A"), say("fresh"))
    await r.turn([user("q1")])
    await r.turn([user("q1"), ChatMessage(role="assistant", content="rewritten by the client"), user("q2")])
    assert "Earlier in this conversation" not in system_text(r.llm.requests[-1])


async def test_the_note_never_reaches_the_client():
    r = kb_rig(call(("kb_search", {"queries": ["cto"]})), say("A"), say("B"))
    await r.turn([user("q1")])
    await r.turn([user("q1"), r.visible_reply(), user("q2")])
    assert "Earlier in this conversation" not in r.sink.content and "kb_search" not in r.sink.content
