from types import SimpleNamespace

import pytest
from telegram import Update

from telegram_bot_gateway.adapters.inbound.telegram import bot as handlers
from telegram_bot_gateway.adapters.inbound.telegram.runtime import Runtime
from telegram_bot_gateway.adapters.inbound.telegram.sender import Sender
from telegram_bot_gateway.application.model import Link, Pending, Session
from telegram_bot_gateway.config import Settings
from tests.fakes import FakeBot, FakeStore, FakeVault, ScriptedLlm

# Covers: FR-INB-01, FR-INB-02, FR-INB-03, FR-INB-04, FR-INB-05, FR-INB-06, FR-INB-07, FR-INB-08, FR-INB-09
# Covers: FR-INB-10, FR-INB-11, FR-INB-12, FR-LNK-01, FR-LNK-02, FR-LNK-03, FR-LNK-07, FR-OPS-03
# Covers: FR-INB-14, AS-01, AS-02, AS-03, AS-04, AS-05
# Covers: FR-CMD-01, FR-CMD-02, FR-CMD-03, FR-CMD-04, FR-CMD-05, FR-CMD-07, FR-CMD-08, FR-ASK-05, FR-ASK-06, FR-SES-03


class Recorder:
    def __init__(self):
        self.jobs = []
        self.stopped = []

    def submit(self, job):
        self.jobs.append(job)

    async def stop(self, key):
        self.stopped.append(key)
        return True


class Env:
    def __init__(self):
        self.bot, self.store = FakeBot(), FakeStore()
        self.rec = Recorder()
        cfg = Settings("t", "http://x", "dflt", "d", "k")
        deps = handlers.Deps(cfg, self.store, ScriptedLlm(), FakeVault(), Sender(self.bot, self.store), self.rec)
        self.ctx = SimpleNamespace(bot=self.bot, args=[], application=SimpleNamespace(bot_data={"deps": deps}))

    async def link(self, uid=5):
        await self.store.save_link(Link(uid, f"user{uid}", "U", "enc:k"))

    async def send(self, handler, update):
        await handler(update, self.ctx)

    def replies(self):
        return [m["text"].replace("\\", "") for m in self.bot.sent]


def update(text="hi", *, chat="private", user=5, thread=None, topic=False, forum=False, entities=None, reply=None,
           forward=None, extra=None, quote=None, bot_user=False, message_id=10):
    chat_id = user if chat == "private" else -100
    msg = {"message_id": message_id, "date": 1_700_000_000, "chat": {"id": chat_id, "type": chat, "is_forum": forum},
           "from": {"id": user, "is_bot": bot_user, "first_name": "Ann"}}
    if text is not None:
        msg["text"] = text
    if entities:
        msg["entities"] = entities
    if thread is not None:
        msg["message_thread_id"] = thread
        msg["is_topic_message"] = topic
    if reply:
        msg["reply_to_message"] = reply
    if forward:
        msg["forward_origin"] = forward
    if quote:
        msg["quote"] = quote
    msg.update(extra or {})
    return Update.de_json({"update_id": message_id, "message": msg}, None)


def mention(text, who="@agentbot"):
    return [{"type": "mention", "offset": text.index(who), "length": len(who)}]


async def test_a_dm_from_a_linked_user_becomes_a_job():
    env = Env()
    await env.link()
    await env.send(handlers.on_message, update("hello"))
    job = env.rec.jobs[0]
    assert job.item.text == "hello" and job.session_key == "5" and job.user_id == 5 and job.target.reply_to == 10


async def test_an_unlinked_dm_gets_the_prompt_and_nothing_is_queued():
    env = Env()
    await env.send(handlers.on_message, update("hello"))
    assert env.rec.jobs == [] and "not linked" in env.replies()[0]


async def test_a_group_message_needs_a_mention_a_reply_to_the_bot_or_a_command():
    env = Env()
    await env.link()
    await env.send(handlers.on_message, update("just chatting", chat="supergroup"))
    assert env.rec.jobs == []
    text = "@agentbot what time is it"
    await env.send(handlers.on_message, update(text, chat="supergroup", entities=mention(text), message_id=11))
    assert env.rec.jobs[0].item.text == "what time is it" and env.rec.jobs[0].session_key == "-100:5"
    reply = {"message_id": 3, "date": 1, "chat": {"id": -100, "type": "supergroup"}, "from": {"id": 999, "is_bot": True, "first_name": "B"}, "text": "earlier"}
    await env.send(handlers.on_message, update("and then?", chat="supergroup", reply=reply, message_id=12))
    assert len(env.rec.jobs) == 2 and env.rec.jobs[1].item.text.startswith('[Replying to: "earlier"]')


async def test_messages_for_other_bots_and_from_bots_are_ignored():
    env = Env()
    await env.link()
    text = "@otherbot hi"
    await env.send(handlers.on_message, update(text, chat="supergroup", entities=mention(text, "@otherbot")))
    await env.send(handlers.on_message, update("hi", bot_user=True, message_id=12))
    assert env.rec.jobs == []


async def test_a_reply_quotes_the_users_own_selection_first():
    env = Env()
    await env.link()
    reply = {"message_id": 3, "date": 1, "chat": {"id": 5, "type": "private"}, "from": {"id": 8, "is_bot": False, "first_name": "B"}, "text": "the whole message"}
    await env.send(handlers.on_message, update("why?", reply=reply, quote={"text": "whole", "position": 4}))
    assert env.rec.jobs[0].item.text == '[Replying to: "whole"]\n\nwhy?'


async def test_a_forwarded_message_says_where_it_came_from():
    env = Env()
    await env.link()
    fwd = {"type": "channel", "date": 1_700_000_000, "chat": {"id": -5, "type": "channel", "title": "News"}, "message_id": 4}
    await env.send(handlers.on_message, update("summarise", forward=fwd))
    assert env.rec.jobs[0].item.text.startswith("[Forwarded from News, 2023-11-14 22:13 UTC]\nsummarise")
    hidden = {"type": "hidden_user", "date": 1_700_000_000, "sender_user_name": "Anonymous Bob"}
    await env.send(handlers.on_message, update("x", forward=hidden, message_id=11))
    assert "Forwarded from Anonymous Bob" in env.rec.jobs[1].item.text
    user = {"type": "user", "date": 1_700_000_000, "sender_user": {"id": 9, "is_bot": False, "first_name": "Cy", "last_name": "Z"}}
    await env.send(handlers.on_message, update("y", forward=user, message_id=12))
    assert "Forwarded from Cy Z" in env.rec.jobs[2].item.text


async def test_topic_threads_are_kept_and_the_topic_root_is_not_a_reply():
    env = Env()
    await env.link()
    root = {"message_id": 42, "date": 1, "chat": {"id": -100, "type": "supergroup"}, "forum_topic_created": {"name": "t", "icon_color": 1}}
    text = "@agentbot hi"
    await env.send(handlers.on_message, update(text, chat="supergroup", thread=42, topic=True, forum=True, entities=mention(text), reply=root))
    job = env.rec.jobs[0]
    assert job.target.thread_id == 42 and job.session_key == "-100:42:5" and "Replying" not in job.item.text


async def test_voice_is_refused_and_never_queued():
    env = Env()
    await env.link()
    await env.send(handlers.on_message, update(None, extra={"voice": {"file_id": "v", "file_unique_id": "u", "duration": 3}}))
    assert env.rec.jobs == [] and "not supported" in env.replies()[0]


async def test_captions_stickers_and_locations_become_text():
    env = Env()
    await env.link()
    await env.send(handlers.on_message, update(None, extra={"caption": "look", "sticker": {"file_id": "s", "file_unique_id": "u", "type": "regular", "width": 1, "height": 1, "is_animated": False, "is_video": False, "emoji": "😀"}}))
    assert env.rec.jobs[0].item.text == "look\n[The user sent a sticker 😀]"
    await env.send(handlers.on_message, update(None, message_id=11, extra={"location": {"latitude": 1.5, "longitude": 2.5}}))
    assert "Location: 1.5, 2.5" in env.rec.jobs[1].item.text


async def test_photos_documents_and_albums_are_attached_and_batched():
    env = Env()
    await env.link()

    async def get_file(file_id):
        return SimpleNamespace(download_as_bytearray=lambda: _aw(b"DATA" if file_id != "t" else b"hello text"))

    async def _aw(v):
        return bytearray(v)

    env.bot.get_file = get_file
    photo = [{"file_id": "p", "file_unique_id": "u", "width": 1, "height": 1}]
    await env.send(handlers.on_message, update(None, extra={"photo": photo, "media_group_id": "g", "caption": "pic"}))
    job = env.rec.jobs[0]
    assert job.item.images[0].startswith("data:image/jpeg;base64,") and job.delay == 0.8 and job.item.text == "pic"
    doc = {"file_id": "t", "file_unique_id": "u", "file_name": "notes.txt", "mime_type": "text/plain", "file_size": 10}
    await env.send(handlers.on_message, update(None, message_id=11, extra={"document": doc}))
    assert env.rec.jobs[1].item.text == "[Content of notes.txt]:\nhello text"
    pdf = {"file_id": "d", "file_unique_id": "u", "file_name": "a.pdf", "mime_type": "application/pdf", "file_size": 5}
    await env.send(handlers.on_message, update(None, message_id=12, extra={"document": pdf}))
    assert "[Document: a.pdf" in env.rec.jobs[2].item.text
    img = {"file_id": "i", "file_unique_id": "u", "file_name": "a.png", "mime_type": "image/png", "file_size": 5}
    await env.send(handlers.on_message, update(None, message_id=13, extra={"document": img}))
    assert env.rec.jobs[3].item.images[0].startswith("data:image/png;base64,")


async def test_a_failed_download_is_reported_not_queued():
    env = Env()
    await env.link()

    async def get_file(file_id):
        raise RuntimeError("too big")

    env.bot.get_file = get_file
    photo = [{"file_id": "p", "file_unique_id": "u", "width": 1, "height": 1}]
    await env.send(handlers.on_message, update(None, extra={"photo": photo}))
    assert env.rec.jobs == [] and "could not download" in env.replies()[0]


async def test_a_group_user_who_is_not_linked_is_told_once():
    env = Env()
    text = "@agentbot hi"
    handlers._group_notices.clear()
    for i in range(2):
        await env.send(handlers.on_message, update(text, chat="supergroup", entities=mention(text), message_id=10 + i))
    assert len(env.bot.sent) == 1 and "privately" in env.replies()[0] and env.rec.jobs == []


async def test_start_pairs_an_unknown_dm_user_with_limits():
    env = Env()
    env.ctx.args = ["ann@example.org"]
    await env.send(handlers.cmd_start, update("/start ann@example.org"))
    code = list(env.store.pairings)[0]
    assert code in env.replies()[0] and env.store.pairings[code].username == "ann@example.org"
    await env.send(handlers.cmd_start, update("/start ann@example.org", message_id=11))
    assert "already asked" in env.replies()[1]
    env.ctx.args = []
    env.store.pairings.clear()
    await env.send(handlers.cmd_start, update("/start", message_id=12))
    assert "not linked" in env.replies()[2] and not env.store.pairings


async def test_start_in_a_group_never_pairs():
    env = Env()
    env.ctx.args = ["x"]
    await env.send(handlers.cmd_start, update("/start x", chat="supergroup"))
    assert not env.store.pairings and "privately" in env.replies()[0]


async def test_start_for_a_linked_user_says_so():
    env = Env()
    await env.link()
    await env.send(handlers.cmd_start, update("/start"))
    assert "linked as user5" in env.replies()[0]


async def test_new_stop_whoami_and_help():
    env = Env()
    await env.link()
    await env.store.save_session(Session("5", 5, messages=[{"role": "user", "content": "x"}]))
    await env.send(handlers.cmd_new, update("/new"))
    assert "5" not in env.store.sessions and env.rec.stopped == ["5"]
    await env.send(handlers.cmd_stop, update("/stop", message_id=11))
    await env.send(handlers.cmd_whoami, update("/whoami", message_id=12))
    who = env.replies()[-1]
    assert "Telegram id: 5" in who and "linked as user5" in who and "Session: 5" in who and "enc:k" not in who
    await env.send(handlers.cmd_help, update("/help", message_id=13))
    assert "/new" in env.replies()[-1]


async def test_commands_in_a_group_act_on_the_senders_own_session():
    env = Env()
    await env.link(5)
    await env.link(6)
    env.ctx.args = ["temperature", "0.4"]
    await env.send(handlers.cmd_set, update("/set temperature 0.4", chat="supergroup", user=5))
    assert env.store.sessions["-100:5"].params == {"temperature": 0.4} and "-100:6" not in env.store.sessions


async def test_settings_commands_validate_and_persist():
    env = Env()
    await env.link()
    env.ctx.args = ["temperature", "9"]
    await env.send(handlers.cmd_set, update("/set temperature 9"))
    assert "between" in env.replies()[-1] and "5" not in env.store.sessions
    env.ctx.args = ["high"]
    await env.send(handlers.cmd_reasoning, update("/reasoning high", message_id=11))
    env.ctx.args = ["agent-b"]
    await env.send(handlers.cmd_model, update("/model agent-b", message_id=12))
    s = env.store.sessions["5"]
    assert s.params == {"reasoning_effort": "high"} and s.model == "agent-b"
    env.ctx.args = ["reasoning_effort"]
    await env.send(handlers.cmd_unset, update("/unset reasoning_effort", message_id=13))
    assert env.store.sessions["5"].params == {}
    env.ctx.args = []
    await env.send(handlers.cmd_settings, update("/settings", message_id=14))
    assert "model: agent-b" in env.replies()[-1] and env.bot.sent[-1]["reply_markup"]
    await env.send(handlers.cmd_reasoning, update("/reasoning", message_id=15))
    labels = [b.text for row in env.bot.sent[-1]["reply_markup"].inline_keyboard for b in row]
    assert labels == ["none", "minimal", "low", "medium", "high", "default"]
    await env.send(handlers.cmd_model, update("/model", message_id=16))
    assert [b.text for row in env.bot.sent[-1]["reply_markup"].inline_keyboard for b in row] == ["agent-a", "agent-b"]


async def test_unlinked_users_cannot_change_settings():
    env = Env()
    env.ctx.args = ["temperature", "1"]
    await env.send(handlers.cmd_set, update("/set temperature 1"))
    assert env.store.sessions == {} and "not linked" in env.replies()[0]


class Query:
    def __init__(self, data, user, message):
        self.data, self.from_user, self.message = data, SimpleNamespace(id=user), message
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


async def card(env, owner=5):
    session = Session("5", owner, pending=Pending("call_1", "Run it?", ["Approve", "Reject"], 5, 1.0, card_message_id=77))
    await env.store.save_session(session)
    message = SimpleNamespace(chat=SimpleNamespace(id=5, type="private", is_forum=False), message_id=77,
                              message_thread_id=None, is_topic_message=False)
    return message


async def test_only_the_owner_can_answer_a_card_and_it_records_the_outcome():
    env = Env()
    message = await card(env)
    stranger = Query("a:0", 6, message)
    await handlers.on_button(SimpleNamespace(callback_query=stranger), env.ctx)
    assert stranger.answers[0][1] is True and env.rec.jobs == []
    owner = Query("a:1", 5, message)
    await handlers.on_button(SimpleNamespace(callback_query=owner), env.ctx)
    assert env.rec.jobs[0].item.text == "Reject" and env.rec.jobs[0].session_key == "5"
    assert "✅ Reject" in env.bot.edits[0]["text"].replace("\\", "") and env.bot.edits[0]["reply_markup"] is None


async def test_other_asks_for_a_message_and_stale_cards_are_refused():
    env = Env()
    message = await card(env)
    other = Query("a:o", 5, message)
    await handlers.on_button(SimpleNamespace(callback_query=other), env.ctx)
    assert "message" in other.answers[0][0] and env.rec.jobs == []
    gone = Query("a:0", 5, SimpleNamespace(chat=message.chat, message_id=1, message_thread_id=None, is_topic_message=False))
    await handlers.on_button(SimpleNamespace(callback_query=gone), env.ctx)
    assert gone.answers[0][1] is True


async def test_picker_buttons_change_the_senders_session():
    env = Env()
    await env.link()
    message = SimpleNamespace(chat=SimpleNamespace(id=5, type="private", is_forum=False), message_id=9, message_thread_id=None, is_topic_message=False)
    await handlers.on_button(SimpleNamespace(callback_query=Query("m:1", 5, message)), env.ctx)
    await handlers.on_button(SimpleNamespace(callback_query=Query("r:low", 5, message)), env.ctx)
    s = env.store.sessions["5"]
    assert s.model == "agent-b" and s.params["reasoning_effort"] == "low"
    await handlers.on_button(SimpleNamespace(callback_query=Query("s:reset", 5, message)), env.ctx)
    assert env.store.sessions["5"].model is None and env.store.sessions["5"].params == {}


def test_repeated_update_ids_are_ignored():
    seen = handlers._Seen()
    assert seen.add(1) is True and seen.add(1) is False and seen.add(2) is True
