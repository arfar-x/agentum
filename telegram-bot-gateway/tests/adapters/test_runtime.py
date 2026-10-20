import asyncio

import pytest

from telegram_bot_gateway.adapters.inbound.telegram.runtime import LINK_PROMPT, Job, Runtime
from telegram_bot_gateway.adapters.inbound.telegram.sender import Sender, Target
from telegram_bot_gateway.adapters.outbound.openai import EndpointError
from telegram_bot_gateway.application.model import Link
from telegram_bot_gateway.config import Settings
from telegram_bot_gateway.domain.content import Item
from tests.fakes import FakeBot, FakeStore, FakeVault, ScriptedLlm, call, say, settle

# Covers: FR-TRN-08, FR-TRN-03, FR-TRN-04, FR-TRN-05, FR-TRN-06, FR-OUT-01, FR-OUT-02, FR-OUT-09, FR-OUT-11
# Covers: FR-INB-13, FR-INB-08, FR-ASK-02, FR-ASK-04, FR-LNK-01


def settings(**kw):
    base = dict(bot_token="t", openai_base_url="http://x", default_model="m", database_url="d", creds_key="k")
    return Settings(**{**base, **kw})


async def make(llm, **kw):
    bot, store = FakeBot(), FakeStore()
    for uid in (5, 6):
        await store.save_link(Link(uid, f"user{uid}", "U", f"enc:key{uid}"))
    sender = Sender(bot, store)
    return bot, store, Runtime(bot=bot, store=store, llm=llm, vault=FakeVault(), sender=sender, config=settings(**kw))


def job(user=5, text="hi", key=None, msg_id=10, delay=0.01, images=()):
    return Job(Target(user, "private", None, msg_id), user, key or str(user), Item(text, tuple(images)), delay)


def texts(bot):
    return [m["text"] for m in bot.sent]


async def test_an_answer_is_sent_with_reactions_and_typing():
    async def a_moment():
        await asyncio.sleep(0.05)
        return say("hello.")

    llm = ScriptedLlm(a_moment)
    bot, store, rt = await make(llm, reactions=True)
    rt.submit(job())
    await settle(lambda: bot.sent and bot.reactions and bot.reactions[-1][1] == ["👍"])
    assert texts(bot) == [r"hello\."]
    assert [r[1] for r in bot.reactions] == [["👀"], ["👍"]] and bot.actions
    assert llm.calls[0]["api_key"] == "key5"
    assert [m["role"] for m in store.sessions["5"].messages] == ["user", "assistant"]


async def test_there_are_no_reactions_by_default():
    bot, _, rt = await make(ScriptedLlm(say("x")))
    rt.submit(job())
    await settle(lambda: bot.sent)
    assert bot.reactions == []


async def test_every_request_starts_with_the_telegram_system_note_which_is_not_stored():
    llm = ScriptedLlm(say("a"), say("b"))
    bot, store, rt = await make(llm)
    rt.submit(job(text="one"))
    await settle(lambda: len(bot.sent) == 1)
    rt.submit(job(text="two", msg_id=11))
    await settle(lambda: len(bot.sent) == 2)
    for sent in llm.calls:
        assert "Telegram" in sent["system"] and "short" in sent["system"]
        assert not any(m["role"] == "system" for m in sent["messages"])
    assert not any(m["role"] == "system" for m in store.sessions["5"].messages)


async def test_the_note_is_shorter_for_groups():
    from telegram_bot_gateway.domain.prompt import telegram_context
    assert "group chat" in telegram_context("supergroup") and "group chat" not in telegram_context("private")


async def test_messages_arriving_together_become_one_turn():
    llm = ScriptedLlm(say("one"))
    bot, _, rt = await make(llm)
    rt.submit(job(text="a", msg_id=1, delay=0.05))
    rt.submit(job(text="b", msg_id=2, delay=0.05))
    await settle(lambda: bot.sent)
    assert len(llm.calls) == 1 and llm.calls[0]["messages"][0]["content"] == "a\nb"


async def test_images_reach_the_endpoint_as_parts():
    llm = ScriptedLlm(say("seen"))
    bot, _, rt = await make(llm)
    rt.submit(job(text="what is this", images=["data:image/png;base64,AA"]))
    await settle(lambda: bot.sent)
    parts = llm.calls[0]["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]


async def test_messages_during_a_turn_queue_and_merge_into_the_next():
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return say("first")

    llm = ScriptedLlm(slow, say("second"))
    bot, store, rt = await make(llm)
    rt.submit(job(text="q1"))
    await settle(lambda: rt.busy("5") and llm.calls)
    rt.submit(job(text="q2", msg_id=11))
    rt.submit(job(text="q3", msg_id=12))
    await asyncio.sleep(0.1)
    assert len(llm.calls) == 1  # still waiting; nothing was interrupted
    gate.set()
    await settle(lambda: len(llm.calls) == 2 and not rt.busy("5"))
    assert llm.calls[1]["messages"][-1]["content"] == "q2\nq3"
    assert texts(bot) == ["first", "second"]


async def test_different_sessions_run_concurrently():
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return say("slow")

    llm = ScriptedLlm(slow, say("fast"))
    bot, _, rt = await make(llm)
    rt.submit(job(user=5, text="a"))
    await settle(lambda: rt.busy("5"))
    rt.submit(job(user=6, text="b"))
    await settle(lambda: texts(bot) == ["fast"])
    gate.set()
    await settle(lambda: len(bot.sent) == 2)


async def test_stop_cancels_the_request_clears_the_reaction_and_saves_nothing():
    async def hang():
        await asyncio.sleep(60)

    llm = ScriptedLlm(hang)
    bot, store, rt = await make(llm, reactions=True)
    rt.submit(job())
    await settle(lambda: rt.busy("5") and llm.calls)
    rt.submit(job(text="queued", msg_id=11, delay=0.01))
    assert await rt.stop("5") is True
    assert not rt.busy("5") and "5" not in store.sessions and bot.sent == []
    assert bot.reactions[-1][1] == []
    await asyncio.sleep(0.1)
    assert len(llm.calls) == 1  # the queue was discarded
    assert await rt.stop("5") is False


async def test_an_endpoint_error_is_shown_and_history_is_unchanged():
    llm = ScriptedLlm(EndpointError("The endpoint answered 400: bad temperature"))
    bot, store, rt = await make(llm, reactions=True)
    rt.submit(job())
    await settle(lambda: bot.sent)
    assert "bad temperature" in texts(bot)[0] and bot.reactions[-1][1] == ["👎"]
    assert "5" not in store.sessions


async def test_an_empty_answer_sends_nothing_and_is_not_a_failure():
    bot, _, rt = await make(ScriptedLlm(say("")), reactions=True)
    rt.submit(job())
    await settle(lambda: bot.reactions and bot.reactions[-1][1] == ["👍"])
    assert bot.sent == []


async def test_an_unlinked_user_never_reaches_the_endpoint():
    llm = ScriptedLlm(say("no"))
    bot, store, rt = await make(llm)
    await store.delete_links(telegram_user_id=5)
    rt.submit(job())
    await settle(lambda: bot.sent)
    assert llm.calls == [] and LINK_PROMPT.split(".")[0] in texts(bot)[0].replace("\\", "")


async def test_a_question_becomes_a_card_with_buttons_and_is_stored_open():
    ask = call("clarify", '{"questions":[{"question":"Which one?","choices":["red","blue"]}]}')
    bot, store, rt = await make(ScriptedLlm(ask))
    rt.submit(job())
    await settle(lambda: store.sessions.get("5") and store.sessions["5"].pending and store.sessions["5"].pending.card_message_id)
    buttons = [b.callback_data for row in bot.sent[0]["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["a:0", "a:1", "a:o"]
    assert (await store.find_pending(5, store.sessions["5"].pending.card_message_id)).key == "5"


async def test_an_approval_card_has_only_approve_and_reject():
    ask = call("clarify", '{"questions":[{"question":"Run it?","choices":["Approve","Reject"]}]}', "approve_1")
    bot, store, rt = await make(ScriptedLlm(ask))
    rt.submit(job())
    await settle(lambda: bot.sent)
    labels = [b.text for row in bot.sent[0]["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Approve", "Reject"]


async def test_the_answer_to_a_question_resumes_the_turn():
    ask = call("clarify", '{"question":"Which?","choices":["red","blue"]}')
    llm = ScriptedLlm(ask, say("red it is"))
    bot, store, rt = await make(llm)
    rt.submit(job())
    await settle(lambda: store.sessions.get("5") and store.sessions["5"].pending and store.sessions["5"].pending.card_message_id)
    rt.submit(job(text="red", msg_id=12))
    await settle(lambda: len(llm.calls) == 2 and store.sessions["5"].pending is None)
    assert llm.calls[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "red"}


async def test_reasoning_progress_is_one_silent_message_removed_afterwards():
    async def streaming():
        return say("done")

    class Llm(ScriptedLlm):
        async def chat(self, api_key, model, messages, params, tools, on_reasoning=None):
            await on_reasoning("thinking about it")
            return await super().chat(api_key, model, messages, params, tools, on_reasoning)

    bot, _, rt = await make(Llm(streaming), show_progress=True)
    rt.submit(job())
    await settle(lambda: bot.deleted)
    progress = bot.sent[0]
    assert progress["disable_notification"] is True and "reply_parameters" not in progress
    assert bot.sent[1]["disable_notification"] is False


async def test_progress_is_off_by_default():
    class Llm(ScriptedLlm):
        async def chat(self, api_key, model, messages, params, tools, on_reasoning=None):
            assert on_reasoning is None
            return say("x")

    bot, _, rt = await make(Llm())
    rt.submit(job())
    await settle(lambda: bot.sent)
    assert bot.deleted == []
