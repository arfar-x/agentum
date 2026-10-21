"""Tests for known defects, written against the corrected behaviour.

Each is an expected failure that names its entry in specs/. When the defect is fixed the test
starts to pass, strict mode turns that into a failure, and the marker is removed with the fix.
Defects whose corrected behaviour still needs a decision (KD-002, KD-003, KD-005, KD-008) have no
test here.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from telegram.error import NetworkError

from telegram_bot_gateway.adapters.inbound.telegram import bot as handlers
from telegram_bot_gateway.application.model import Pending, Session
from tests.adapters.test_bot import Env, Query, update
from tests.adapters.test_runtime import job, make
from tests.fakes import ScriptedLlm, say


@pytest.mark.xfail(strict=True, reason="KD-001: the reply is saved to the history before it is sent")
async def test_kd001_an_answer_that_could_not_be_sent_is_not_kept_in_the_history():
    bot, store, rt = await make(ScriptedLlm(say("an answer the user never sees")))
    bot.fail_next = [NetworkError("down") for _ in range(10)]
    rt.submit(job())
    await asyncio.sleep(0.4)
    saved = store.sessions.get("5")
    assert saved is None or all(m["role"] != "assistant" for m in saved.messages)


@pytest.mark.xfail(strict=True, reason="KD-004: a card older than 24 hours is accepted at the tap")
async def test_kd004_a_card_older_than_a_day_is_refused_at_the_tap():
    env = Env()
    session = Session("5", 5, pending=Pending("call_1", "Run it?", ["Approve", "Reject"], 5, time.time() - 90_000, card_message_id=77))
    await env.store.save_session(session)
    message = SimpleNamespace(chat=SimpleNamespace(id=5, type="private", is_forum=False), message_id=77,
                              message_thread_id=None, is_topic_message=False)
    tap = Query("a:0", 5, message)
    await handlers.on_button(SimpleNamespace(callback_query=tap), env.ctx)
    assert env.rec.jobs == [] and tap.answers and tap.answers[0][1] is True


@pytest.mark.xfail(strict=True, reason="KD-006: /set refuses reasoning_effort although /unset accepts it")
async def test_kd006_set_and_unset_accept_the_same_names():
    env = Env()
    await env.link()
    env.ctx.args = ["reasoning_effort", "high"]
    await env.send(handlers.cmd_set, update("/set reasoning_effort high"))
    assert env.store.sessions["5"].params == {"reasoning_effort": "high"}


@pytest.mark.xfail(strict=True, reason="KD-007: /model cuts a long list without saying so")
async def test_kd007_a_cut_model_list_says_how_many_there_are():
    env = Env()
    await env.link()

    async def many(api_key):
        return [f"model-{i}" for i in range(50)]

    env.ctx.application.bot_data["deps"].llm.models = many
    await env.send(handlers.cmd_model, update("/model"))
    assert "50" in env.replies()[-1]
