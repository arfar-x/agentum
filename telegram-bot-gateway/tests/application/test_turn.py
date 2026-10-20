import pytest

from telegram_bot_gateway.application import commands, linking, turn
from telegram_bot_gateway.application.model import Link, PENDING_TTL_SECONDS, Pending, Session
from tests.fakes import FakeStore, FakeVault, ScriptedLlm, call, say

# Covers: FR-TRN-01, FR-TRN-02, FR-TRN-06, FR-SES-02, FR-SES-04, FR-ASK-01, FR-ASK-03, FR-ASK-07
# Covers: FR-LNK-01, FR-LNK-02, FR-LNK-03, FR-LNK-04, FR-LNK-05, FR-LNK-06, FR-CMD-05, FR-CMD-06

NOW = 1_000_000.0


async def run(session, llm, content="hi"):
    work, stale = turn.begin(session, content, now=NOW)
    outcome = await turn.run(work, llm, "key-1", default_model="dflt", chat_id=7, now=NOW)
    return work, outcome


async def test_a_turn_sends_history_model_params_and_the_clarify_tool():
    llm = ScriptedLlm(say("hello"))
    session = Session("7", 7, model="agent-x", params={"temperature": 0.2})
    work, outcome = await run(session, llm)
    assert outcome.text == "hello"
    sent = llm.calls[0]
    assert sent["model"] == "agent-x" and sent["params"] == {"temperature": 0.2} and sent["api_key"] == "key-1"
    assert [t["function"]["name"] for t in sent["tools"]] == ["clarify"]
    assert work.messages == [{"role": "user", "content": "hi"}, say("hello")]
    assert session.messages == []  # the caller's session is untouched until it saves


async def test_the_default_model_is_used_when_none_is_set():
    llm = ScriptedLlm(say("x"))
    await run(Session("7", 7), llm)
    assert llm.calls[0]["model"] == "dflt"


async def test_history_is_replayed_exactly_including_tool_calls():
    first = call("clarify", '{"questions":[{"question":"Which?","choices":["a","b"]}]}')
    llm = ScriptedLlm(first, say("done"))
    work, outcome = await run(Session("7", 7), llm)
    assert isinstance(outcome, turn.Ask) and outcome.question.options == ("a", "b")
    assert work.pending.tool_call_id == "call_1" and work.pending.chat_id == 7
    # the user's next message answers the open question
    work2, _ = turn.begin(work, "b", now=NOW + 5)
    assert work2.messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "b"}
    assert work2.pending is None
    await turn.run(work2, llm, "key-1", default_model="d", chat_id=7, now=NOW)
    assert llm.calls[1]["messages"][1] == first  # byte-for-byte as returned


async def test_a_question_older_than_a_day_is_dropped_with_its_turn():
    work, _ = await run(Session("7", 7), ScriptedLlm(call("clarify", '{"question":"?"}')))
    work.pending.card_message_id = 55
    later, stale = turn.begin(work, "new", now=NOW + PENDING_TTL_SECONDS + 1)
    assert stale is not None and stale.card_message_id == 55 and later.pending is None
    assert later.messages == [{"role": "user", "content": "hi"}, {"role": "user", "content": "new"}]


async def test_unsupported_tools_are_answered_and_reported():
    llm = ScriptedLlm(call("shell", "{}", "c9"), say("fine"))
    work, outcome = await run(Session("7", 7), llm)
    assert outcome.text == "fine" and "unsupported tool: shell" in outcome.notices[0]
    assert {"role": "tool", "tool_call_id": "c9", "content": turn.UNSUPPORTED_TOOL} in work.messages


async def test_an_endpoint_error_leaves_the_stored_history_alone():
    session = Session("7", 7, messages=[{"role": "user", "content": "old"}])
    with pytest.raises(RuntimeError):
        await run(session, ScriptedLlm(RuntimeError("boom")))
    assert session.messages == [{"role": "user", "content": "old"}]


def test_images_of_earlier_turns_are_dropped_but_the_new_turn_keeps_them():
    img = [{"type": "text", "text": "see"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
    session = Session("7", 7, messages=[{"role": "user", "content": img}])
    work, _ = turn.begin(session, img, now=NOW)
    assert work.messages[0]["content"] == "see" and work.messages[1]["content"] == img


async def test_pairing_flow():
    store, vault = FakeStore(), FakeVault()
    paired = await linking.request(store, telegram_user_id=5, username="ann", telegram_name="Ann", chat_id=5, now=NOW)
    assert len(paired.code) == 8
    assert (await linking.request(store, telegram_user_id=5, username="ann", telegram_name="Ann", chat_id=5, now=NOW + 60)).reason == "too_soon"
    for i in range(2):
        await linking.request(store, telegram_user_id=5, username="ann", telegram_name="Ann", chat_id=5, now=NOW + 700 * (i + 1))
    assert (await linking.request(store, telegram_user_id=5, username="ann", telegram_name="Ann", chat_id=5, now=NOW + 2200)).reason == "too_many"
    assert await linking.api_key_for(store, vault, 5) is None
    found = await linking.approve(store, vault, paired.code.lower(), "agw_secret", now=NOW + 10)
    assert found.username == "ann"
    assert store.links[5].api_key_enc != "agw_secret"  # stored encrypted
    assert await linking.api_key_for(store, vault, 5) == "agw_secret"
    assert paired.code not in store.pairings


async def test_an_expired_or_unknown_code_is_refused():
    store = FakeStore()
    paired = await linking.request(store, telegram_user_id=5, username="a", telegram_name="A", chat_id=5, now=NOW)
    with pytest.raises(LookupError):
        await linking.approve(store, FakeVault(), paired.code, "k", now=NOW + 3601)
    with pytest.raises(LookupError):
        await linking.approve(store, FakeVault(), "NOPE", "k", now=NOW)


async def test_unlinking_removes_the_link_and_the_sessions():
    store = FakeStore()
    await store.save_link(Link(5, "ann", "Ann", "enc:k"))
    await store.save_session(Session("5", 5))
    await store.save_session(Session("-1:5", 5))
    await store.save_session(Session("6", 6))
    assert await linking.unlink(store, username="ann") == 1
    assert list(store.sessions) == ["6"] and not store.links


def test_settings_commands():
    s = Session("7", 7)
    assert commands.set_param(s, "temperature", "0.3") == "temperature = 0.3"
    assert commands.set_reasoning(s, "high") == "reasoning = high"
    commands.set_model(s, "agent-b")
    text = commands.describe(s, "dflt")
    assert "agent-b" in text and "temperature: 0.3" in text and "reasoning_effort: high" in text
    commands.unset_param(s, "temperature")
    commands.set_reasoning(s, "default")
    assert s.params == {}
    with pytest.raises(ValueError):
        commands.set_param(s, "temperature", "9")
    commands.reset_settings(s)
    assert s.model is None and "default" in commands.describe(s, "dflt")
