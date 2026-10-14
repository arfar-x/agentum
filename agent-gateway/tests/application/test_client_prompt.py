# Covers: FR-AGT-07, FR-AGT-08, FR-AGT-09

from agent_gateway.domain.messages import ChatMessage
from tests.application.fakes import agent, rig, say, user

LONG = "You are Some Other Assistant. " + ("Always prefer the todo tool; run shell commands carefully. " * 200)   # ~12k chars
SHORT = "Answer in the user's language."


def system(text):
    return ChatMessage(role="system", content=text)


def prompt_sent(r, index=-1):
    return r.llm.requests[index].messages[0].text()


async def test_a_short_client_prompt_is_used_as_sent_with_no_extra_model_call():
    r = rig(say("ok"))
    await r.turn([system(SHORT), user("hi")])
    assert len(r.llm.requests) == 1 and SHORT in prompt_sent(r)


async def test_a_long_client_prompt_is_condensed_once_and_the_condensed_text_replaces_it():
    r = rig(say("- use the todo tool\n- be careful with shell"), say("answer"))
    await r.turn([system(LONG), user("hi")])
    compaction_call, turn_call = r.llm.requests
    assert compaction_call.tools == [] and LONG in compaction_call.messages[1].text()
    assert "Be helpful." not in compaction_call.messages[0].text()          # the agent's own prompt is not involved
    sent = turn_call.messages[0].text()
    assert "use the todo tool" in sent and LONG not in sent and len(sent) < len(LONG) / 2
    assert "<client_instructions>" in sent                                      # still marked lower priority


async def test_the_condensed_prompt_is_cached_so_later_turns_make_no_compaction_call():
    r = rig(say("- condensed rules"), say("one"), say("two"))
    await r.turn([system(LONG), user("hi")])
    await r.turn([system(LONG), user("again")])
    assert len(r.llm.requests) == 3                                             # 1 compaction + 2 turns
    assert "condensed rules" in prompt_sent(r)


async def test_another_users_cache_entry_is_never_used():
    r = rig(say("- rules for u1"), say("a"), say("- rules for u2"), say("b"))
    await r.turn([system(LONG), user("hi")], user="u1")
    await r.turn([system(LONG), user("hi")], user="u2")
    assert "rules for u2" in prompt_sent(r) and "rules for u1" not in prompt_sent(r)
    assert len(r.llm.requests) == 4


async def test_a_failed_or_useless_compaction_truncates_and_the_turn_still_succeeds():
    for bad in (RuntimeError("model down"), say(""), say(LONG + " even longer")):
        r = rig(bad, say("answer"))
        await r.turn([system(LONG), user("hi")])
        sent = prompt_sent(r)
        assert r.sink.content == "answer" and "[... shortened ...]" in sent and len(sent) < len(LONG) / 2
        assert sent.index("Be helpful.") < sent.index("Some Other Assistant")


async def test_a_new_client_prompt_invalidates_the_users_older_cached_one():
    other = LONG.replace("Some Other", "Another")
    r = rig(say("- rules one"), say("a"), say("- rules two"), say("b"), say("- rules one again"), say("c"))
    await r.turn([system(LONG), user("hi")])
    assert len(r.deps.cache.data) == 1
    await r.turn([system(other), user("hi")])                       # a different prompt arrives
    assert len(r.deps.cache.data) == 1 and list(r.deps.cache.data.values()) == ["- rules two"]
    await r.turn([system(LONG), user("hi")])                        # the first one was invalidated: condensed again
    assert "rules one again" in prompt_sent(r) and len(r.llm.requests) == 6


async def test_more_entries_can_be_kept_per_user_when_configured():
    import dataclasses

    other = LONG.replace("Some Other", "Another")
    r = rig(say("- one"), say("a"), say("- two"), say("b"), say("c"), say("d"))
    r.deps.settings = dataclasses.replace(r.deps.settings, client_prompt_cache_entries=2)
    await r.turn([system(LONG), user("hi")])
    await r.turn([system(other), user("hi")])
    await r.turn([system(LONG), user("hi")])                        # both still cached: no new compaction
    await r.turn([system(other), user("hi")])
    assert len(r.llm.requests) == 6 and len(r.deps.cache.data) == 2
