import pytest
from telegram.error import BadRequest, RetryAfter

from telegram_bot_gateway.adapters.inbound.telegram.sender import Sender, Target
from tests.fakes import FakeBot, FakeStore

# Covers: FR-OUT-04, FR-OUT-05, FR-OUT-06, FR-OUT-07, FR-OUT-08, FR-OUT-10, FR-SES-05


def make():
    bot, store = FakeBot(), FakeStore()
    return bot, store, Sender(bot, store)


async def test_the_answer_is_sent_as_markdown_v2_and_remembered():
    bot, store, sender = make()
    ids = await sender.send(Target(1, "private", None, 5), "hi.")
    assert bot.sent[0]["text"] == r"hi\." and bot.sent[0]["parse_mode"] == "MarkdownV2"
    assert store.texts[(1, ids[0])] == "hi."


async def test_rejected_formatting_is_resent_as_plain_text():
    bot, _, sender = make()
    bot.fail_next.append(BadRequest("Can't parse entities: character '.' must be escaped"))
    await sender.send(Target(1, "private"), "**hi**")
    assert bot.sent[0]["text"] == "hi" and "parse_mode" not in bot.sent[0]


async def test_a_missing_thread_is_retried_without_it():
    bot, _, sender = make()
    bot.fail_next.append(BadRequest("Message thread not found"))
    await sender.send(Target(1, "supergroup", 42), "hi")
    assert "message_thread_id" not in bot.sent[0]


async def test_threads_are_passed_and_unrelated_errors_propagate():
    bot, _, sender = make()
    await sender.send(Target(1, "supergroup", 42), "hi")
    assert bot.sent[0]["message_thread_id"] == 42
    bot.fail_next.append(BadRequest("chat not found"))
    with pytest.raises(BadRequest):
        await sender.send(Target(1, "private"), "x")


async def test_flood_control_is_waited_out(monkeypatch):
    waited = []

    async def fake_sleep(s):
        waited.append(s)

    monkeypatch.setattr("telegram_bot_gateway.adapters.inbound.telegram.sender.asyncio.sleep", fake_sleep)
    bot, _, sender = make()
    bot.fail_next.append(RetryAfter(3))
    await sender.send(Target(1, "private"), "hi")
    assert waited and waited[0] >= 3 and bot.sent


async def test_only_the_first_part_replies_and_all_notify_by_default():
    bot, _, sender = make()
    await sender.send(Target(1, "private", None, 5), "\n".join(f"line {i} of text." for i in range(800)))
    assert len(bot.sent) > 1
    assert bot.sent[0]["reply_parameters"].message_id == 5
    assert all("reply_parameters" not in m for m in bot.sent[1:])
    assert all(m["disable_notification"] is False for m in bot.sent)


async def test_no_reply_in_a_group_topic_and_silent_messages():
    bot, _, sender = make()
    await sender.send(Target(1, "supergroup", 42, 5), "hi", notify=False)
    assert "reply_parameters" not in bot.sent[0] and bot.sent[0]["disable_notification"] is True
    await sender.send(Target(2, "private", 7, 5), "hi")  # DM topics do reply
    assert bot.sent[1]["reply_parameters"].message_id == 5
