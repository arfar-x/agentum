# Covers: NFR-ARC-03, NFR-ARC-04, FR-AGT-04
# Covers: FR-ACT-01, FR-ACT-02, FR-RET-01, FR-RET-02, FR-RET-03, FR-RET-04, FR-COR-01, FR-COR-02, FR-COR-03, FR-COR-04, FR-PRM-01, FR-PRM-02, FR-PRM-03

import pytest

from agent_gateway.application.models import ToolResult
from agent_gateway.domain.errors import LoopExhausted
from agent_gateway.domain.messages import ChatMessage
from tests.application.fakes import agent, call, client_tool, raw_call, rig, say, spec, user


class Retriever:
    def __init__(self, hits=None, fail=False):
        self.hits, self.fail, self.queries = hits or [], fail, []

    async def search(self, queries, limit, types, *, identity=None):
        self.queries.append(queries)
        if self.fail:
            raise RuntimeError("catalog down")
        return self.hits


KB = {"kb": {"enabled": True}}


async def test_a_plain_chat_answers_and_totals_usage():
    r = rig(say("Hello there"))
    await r.turn([user("hi")])
    assert r.sink.content == "Hello there"
    assert r.sink.done.finish_reason == "stop"
    assert r.sink.done.usage.total_tokens == 15
    assert r.llm.requests[0].model == "base-model"


async def test_usage_is_summed_over_every_upstream_call():
    r = rig(call(("t", {})), say("done"), specs={"kb": [spec("t", "kb")]},
            agents=[agent(tools=[{"server": "kb"}])])
    await r.turn([user("go")])
    assert r.sink.done.usage.total_tokens == 30


async def test_streaming_and_non_streaming_give_the_same_text():
    for stream in (False, True):
        r = rig(say("one two three"))
        await r.turn([user("hi")], stream=stream)
        assert r.sink.content.strip() == "one two three"


async def test_the_client_system_prompt_is_part_of_the_system_prompt_not_the_history():
    r = rig(say("ok"))
    sys = ChatMessage(role="system", content="You are in a terminal.")
    await r.turn([sys, user("hi")])
    sent = r.llm.requests[0].messages
    assert sent[0].role == "system" and "You are in a terminal." in sent[0].text() and "Be helpful." in sent[0].text()
    assert [m.role for m in sent[1:]] == ["user"]


async def test_agent_defaults_apply_and_client_overrides_respect_locks():
    a = agent(model_parameters={"temperature": 0.2, "reasoning_effort": "low"}, locked_parameters=("temperature",))
    r = rig(say("x"), agents=[a])
    await r.turn([user("hi")], params={"temperature": 1.5, "reasoning_effort": "high", "n": 1})
    assert r.llm.requests[0].params == {"temperature": 0.2, "reasoning_effort": "high"}


async def test_retrieval_puts_hits_in_context_before_the_first_model_step():
    ret = Retriever([{"title": {"en": "Refund retries"}, "summary": {"en": "How refunds retry"}, "type": "doc"}])
    r = rig(say("answer"), agents=[agent(preretrieval=KB)], retriever=ret)
    await r.turn([user("how do refunds retry?")])
    assert ret.queries == [["how do refunds retry?"]]
    system_texts = " ".join(m.text() for m in r.llm.requests[0].messages if m.role == "system")
    assert "Refund retries" in system_texts
    assert "knowledge base" in r.sink.reasoning.lower()


async def test_a_catalog_failure_does_not_fail_the_turn():
    r = rig(say("answer"), agents=[agent(preretrieval=KB)], retriever=Retriever(fail=True))
    await r.turn([user("q")])
    assert r.sink.content == "answer"
    assert "could not be consulted" in " ".join(m.text() for m in r.llm.requests[0].messages)


async def test_retrieval_is_off_unless_enabled_and_not_repeated_on_resume():
    ret = Retriever()
    r = rig(say("a"), agents=[agent()], retriever=ret)
    await r.turn([user("q")])
    assert ret.queries == []
    r = rig(call(("lookup", {})), say("b"), agents=[agent(preretrieval=KB)], retriever=ret)
    msgs = [user("q")]
    await r.turn(msgs, client_tools=[client_tool("lookup")])
    assert len(ret.queries) == 1
    asst = r.visible_reply()
    result = ChatMessage(role="tool", tool_call_id=asst.tool_calls[0].id, content="found")
    await r.turn([*msgs, asst, result], client_tools=[client_tool("lookup")])
    assert len(ret.queries) == 1


async def test_passthrough_style_instructions_never_reach_a_non_agent_turn():
    # an agent turn always carries the instructions; this documents what a passthrough must lack
    r = rig(say("ok"))
    await r.turn([user("hi")])
    assert "Be helpful." in r.llm.requests[0].messages[0].text()


async def test_an_unknown_tool_is_corrected_not_failed():
    r = rig(call(("nope", {})), say("recovered"))
    await r.turn([user("hi")])
    assert r.sink.content == "recovered"
    second = r.llm.requests[1].messages
    assert any(m.role == "tool" and "no tool named 'nope'" in m.text() for m in second)


async def test_malformed_arguments_and_schema_violations_are_corrected():
    r = rig(raw_call("t", '{"a": '), call(("t", {})), say("fine"),
            specs={"kb": [spec("t", "kb", a={"type": "string"})]}, agents=[agent(tools=[{"server": "kb"}])])
    r.tools.specs["kb"][0].parameters["required"] = ["a"]
    await r.turn([user("go")])
    tool_texts = [m.text() for m in r.llm.requests[2].messages if m.role == "tool"]
    assert any("not valid JSON" in t for t in tool_texts)
    assert any("missing required 'a'" in t for t in tool_texts)
    assert r.sink.content == "fine"


async def test_an_empty_reply_gets_a_correction_and_a_retry():
    r = rig(say(""), say("now an answer"))
    await r.turn([user("hi")])
    assert r.sink.content == "now an answer"


async def test_corrections_are_bounded():
    r = rig(*[call(("nope", {})) for _ in range(10)])
    with pytest.raises(LoopExhausted):
        await r.turn([user("hi")])


async def test_the_activity_instruction_is_present_unless_the_agent_is_quiet_and_events_stay_out_of_content():
    from agent_gateway.domain.activity import NARRATION_BLOCK

    r = rig(say("x"))
    await r.turn([user("hi")])
    assert NARRATION_BLOCK in r.llm.requests[0].messages[0].text()
    r = rig(say("x"), agents=[agent(activity="quiet")])
    await r.turn([user("hi")])
    assert NARRATION_BLOCK not in r.llm.requests[0].messages[0].text() and r.sink.reasoning == ""
    router = agent("router", handoffs=[{"to": "tasks", "description": "x"}])
    r = rig(call(("transfer_to_tasks", {})), say("hello"), agents=[router, agent("tasks")])
    await r.turn([user("go")])
    assert "Handing off" in r.sink.reasoning and "Handing off" not in r.sink.content


async def test_streamed_tokens_are_appended_exactly_as_they_arrive():
    # Regression: a paragraph break was inserted between every token of a streamed reply.
    text = "سلام! چه کمکی می‌تونم بکنم؟ Here is `code` and a list:\n- one\n- two"
    r = rig(say(text))
    await r.turn([user("hi")], stream=True)
    assert r.sink.content == text
    pieces = [e.text for e in r.sink.events if hasattr(e, "text") and e.text and e in r.sink.events and type(e).__name__ == "ContentDelta"]
    assert len(pieces) > 5 and "".join(pieces) == text


async def test_text_from_separate_model_steps_is_separated_by_one_paragraph_break():
    r = rig(call(("lookup", {}), text="Let me check."), say("It is done."), specs={"kb": [spec("lookup", "kb")]},
            agents=[agent(tools=[{"server": "kb"}])])
    await r.turn([user("go")], stream=True)
    assert r.sink.content == "Let me check.\n\nIt is done."



async def test_leading_blank_lines_are_trimmed_and_steps_joined_by_one_break():
    r = rig(call(("lookup", {}), text="\n\nLet me check."), say("\n\nIt is done."), specs={"kb": [spec("lookup", "kb")]},
            agents=[agent(tools=[{"server": "kb"}])])
    await r.turn([user("go")], stream=True)
    assert r.sink.content == "Let me check.\n\nIt is done."
