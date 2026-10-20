"""The Telegram handlers: who may talk, what a message means, and the commands."""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import Conflict
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
    TypeHandler, filters,
)

from telegram_bot_gateway.adapters.inbound.telegram import media
from telegram_bot_gateway.adapters.inbound.telegram.runtime import LINK_PROMPT, Job, Runtime
from telegram_bot_gateway.adapters.inbound.telegram.sender import Sender, Target
from telegram_bot_gateway.adapters.outbound.openai import EndpointError
from telegram_bot_gateway.application import commands, linking
from telegram_bot_gateway.application.model import Session
from telegram_bot_gateway.application.ports import Llm, Store, Vault
from telegram_bot_gateway.config import Settings
from telegram_bot_gateway.domain import batching, content, settings
from telegram_bot_gateway.domain.session import effective_thread_id, session_key
from telegram_bot_gateway.domain.triggers import Incoming, is_trigger

log = logging.getLogger(__name__)

GROUP_NOTICE_INTERVAL = 600
SEEN_UPDATES = 4096
MODEL_BUTTONS = 40
HELP = (
    "Send me a message and I answer it with your own account.\n\n"
    "/new  start a fresh conversation\n/stop  cancel what I am working on\n"
    "/model  pick the model\n/reasoning  set the reasoning effort\n"
    "/set <name> <value>, /unset <name>  tune the model (temperature, top_p, max_tokens, seed, "
    "verbosity, presence_penalty, frequency_penalty, stop)\n/settings  show what is set\n/whoami  who I think you are"
)
MENU = [
    BotCommand("new", "Start a new conversation"), BotCommand("stop", "Cancel the running request"),
    BotCommand("model", "Choose the model"), BotCommand("reasoning", "Set the reasoning effort"),
    BotCommand("set", "Set a generation parameter"), BotCommand("unset", "Reset a generation parameter"),
    BotCommand("settings", "Show current settings"), BotCommand("whoami", "Show who you are linked as"),
    BotCommand("help", "What I can do"),
]


@dataclass
class Deps:
    config: Settings
    store: Store
    llm: Llm
    vault: Vault
    sender: Sender
    runtime: Runtime


def _deps(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.application.bot_data["deps"]


@dataclass(frozen=True)
class Where:
    target: Target
    key: str
    user_id: int
    is_group: bool


def locate(message: Message, user_id: int) -> Where:
    chat = message.chat
    thread = effective_thread_id(
        thread_id=message.message_thread_id, is_topic_message=bool(message.is_topic_message), is_forum=bool(chat.is_forum)
    )
    key = session_key(chat_type=chat.type, chat_id=chat.id, thread_id=thread, user_id=user_id)
    return Where(Target(chat.id, chat.type, thread, message.message_id), key, user_id, chat.type != "private")


def real_reply(message: Message) -> Message | None:
    """The message replied to, not the topic's root that Telegram attaches to every topic message."""
    reply = message.reply_to_message
    if reply is None or reply.forum_topic_created is not None:
        return None
    if message.is_topic_message and reply.message_id == message.message_thread_id:
        return None
    return reply


def _flags(message: Message, bot_id: int, username: str) -> Incoming:
    entities = message.parse_entities(["mention", "text_mention"]) or message.parse_caption_entities(["mention", "text_mention"])
    mentions = [e for e in entities.items()]
    ours = f"@{username}".lower()
    mentions_bot = any((e.user and e.user.id == bot_id) or t.lower() == ours for e, t in mentions)
    other_bots = any(
        (e.user and e.user.is_bot and e.user.id != bot_id) or (e.type == "mention" and t.lower().endswith("bot") and t.lower() != ours)
        for e, t in mentions
    )
    reply = real_reply(message)
    replies_to_bot = bool(reply and reply.from_user and reply.from_user.id == bot_id)
    return Incoming(message.chat.type, bool(message.from_user and message.from_user.is_bot), mentions_bot, other_bots, replies_to_bot)


# -- linking gate ------------------------------------------------------------------------------
_group_notices: dict[int, float] = {}


async def _require_link(update: Update, context: ContextTypes.DEFAULT_TYPE, where: Where) -> bool:
    deps = _deps(context)
    if await deps.store.get_link(where.user_id) is not None:
        return True
    if not where.is_group:
        await deps.sender.send(where.target, LINK_PROMPT)
    else:
        now = time.monotonic()
        if now - _group_notices.get(where.user_id, -GROUP_NOTICE_INTERVAL) >= GROUP_NOTICE_INTERVAL:
            _group_notices[where.user_id] = now
            await deps.sender.send(where.target, "Message me privately to link your account first.")
    return False


# -- messages ----------------------------------------------------------------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if message is None or user is None:
        return
    deps, bot = _deps(context), context.bot
    flags = _flags(message, bot.id, bot.username)
    if not is_trigger(flags):
        return
    where = locate(message, user.id)
    if media.has_voice(message):
        await deps.sender.send(where.target, "Voice notes are not supported yet; please type your message.")
        return
    if not await _require_link(update, context, where):
        return

    text = message.text or message.caption or ""
    if where.is_group and not flags.mentions_other_bots:
        text = re.sub(rf"@{re.escape(bot.username)}\b", "", text, flags=re.IGNORECASE).strip()
    reply = real_reply(message)
    quoted = None
    if reply is not None:
        quoted = (message.quote.text if message.quote and message.quote.text else None) or reply.text or reply.caption
        quoted = quoted or await deps.store.get_text(message.chat.id, reply.message_id)
    try:
        own = await media.media_item(message, bot)
        theirs = await media.media_item(reply, bot) if reply is not None else content.Item()
    except media.DownloadError:
        await deps.sender.send(where.target, "I could not download that file; please try sending it again.")
        return
    body = content.compose(
        "\n".join(p for p in (text, own.text) if p), quoted=quoted, forwarded=media.forward_origin(message)
    )
    if theirs.text:
        body += f"\n{theirs.text}"
    item = content.Item(body, own.images + theirs.images)
    if not (item.text or item.images):
        return
    await deps.store.save_text(message.chat.id, message.message_id, body)
    delay = batching.MEDIA_DELAY if item.images or message.media_group_id else batching.text_delay(len(text))
    deps.runtime.submit(Job(where.target, user.id, where.key, item, delay))


# -- commands ----------------------------------------------------------------------------------
async def _ctx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[Deps, Where] | None:
    message, user = update.effective_message, update.effective_user
    if message is None or user is None:
        return None
    return _deps(context), locate(message, user.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    got = await _ctx(update, context)
    if got is None:
        return
    deps, where = got
    user = update.effective_user
    link = await deps.store.get_link(user.id)
    if link is not None:
        await deps.sender.send(where.target, f"You are linked as {link.username}. Send me a message to begin.")
        return
    if where.is_group:
        await deps.sender.send(where.target, "Message me privately to link your account first.")
        return
    username = " ".join(context.args or []).strip()
    if not username:
        await deps.sender.send(where.target, LINK_PROMPT)
        return
    result = await linking.request(
        deps.store, telegram_user_id=user.id, username=username, telegram_name=user.full_name,
        chat_id=where.target.chat_id, now=time.time(),
    )
    if isinstance(result, linking.Paired):
        await deps.sender.send(
            where.target,
            f"Your code is `{result.code}`. Give it to the operator, who links you with `make telegram-pair CODE={result.code}`. "
            "It is valid for one hour.",
        )
    elif result.reason == "too_soon":
        await deps.sender.send(where.target, "You already asked recently; wait a few minutes or use the code I gave you.")
    else:
        await deps.sender.send(where.target, "You have too many open codes. Ask the operator to approve one of them.")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    got = await _ctx(update, context)
    if got is None or not await _require_link(update, context, got[1]):
        return
    deps, where = got
    await deps.runtime.stop(where.key)
    await deps.store.delete_session(where.key)
    await deps.sender.send(where.target, "Started a new conversation.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    got = await _ctx(update, context)
    if got is None:
        return
    deps, where = got
    stopped = await deps.runtime.stop(where.key)
    await deps.sender.send(where.target, "Stopped." if stopped else "Nothing is running.")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    got = await _ctx(update, context)
    if got is None:
        return
    deps, where = got
    link = await deps.store.get_link(where.user_id)
    who = f"linked as {link.username}" if link else "not linked"
    await deps.sender.send(where.target, f"Telegram id: {where.user_id}\nAccount: {who}\nSession: {where.key}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    got = await _ctx(update, context)
    if got is not None:
        await got[0].sender.send(got[1].target, HELP)


async def _session(deps: Deps, where: Where) -> Session:
    return await deps.store.load_session(where.key) or Session(where.key, where.user_id)


async def _change(update: Update, context: ContextTypes.DEFAULT_TYPE, apply) -> None:
    """Run a settings change on the sender's session and say what happened."""
    got = await _ctx(update, context)
    if got is None or not await _require_link(update, context, got[1]):
        return
    deps, where = got
    session = await _session(deps, where)
    try:
        reply = apply(session, context.args or [])
    except ValueError as exc:
        reply = str(exc)
    else:
        await deps.store.save_session(session)
    await deps.sender.send(where.target, reply)


async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def apply(s: Session, args: list[str]) -> str:
        if len(args) < 2:
            raise ValueError("usage: /set <name> <value>; names: " + ", ".join(settings.PARAMS))
        return commands.set_param(s, args[0], " ".join(args[1:]))
    await _change(update, context, apply)


async def cmd_unset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    def apply(s: Session, args: list[str]) -> str:
        if len(args) != 1:
            raise ValueError("usage: /unset <name>")
        return commands.unset_param(s, args[0])
    await _change(update, context, apply)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    got = await _ctx(update, context)
    if got is None or not await _require_link(update, context, got[1]):
        return
    deps, where = got
    text = commands.describe(await _session(deps, where), deps.config.default_model)
    await deps.sender.send(where.target, text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Reset", callback_data="s:reset")]]))


async def cmd_reasoning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        await _change(update, context, lambda s, a: commands.set_reasoning(s, a[0]))
        return
    got = await _ctx(update, context)
    if got is None or not await _require_link(update, context, got[1]):
        return
    levels = [*settings.REASONING_EFFORTS, "default"]
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"r:{l}") for l in levels[:3]],
                                   [InlineKeyboardButton(l, callback_data=f"r:{l}") for l in levels[3:]]])
    await got[0].sender.send(got[1].target, "Reasoning effort:", reply_markup=markup)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        await _change(update, context, lambda s, a: commands.set_model(s, a[0]))
        return
    got = await _ctx(update, context)
    if got is None or not await _require_link(update, context, got[1]):
        return
    deps, where = got
    key = await linking.api_key_for(deps.store, deps.vault, where.user_id) or deps.config.openai_api_key
    try:
        models = (await deps.llm.models(key))[:MODEL_BUTTONS]
    except EndpointError as exc:
        await deps.sender.send(where.target, str(exc))
        return
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(m, callback_data=f"m:{i}")] for i, m in enumerate(models)])
    await deps.sender.send(where.target, "Choose a model:", reply_markup=markup)


# -- buttons -----------------------------------------------------------------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None or not query.data:
        return
    deps = _deps(context)
    kind, _, value = query.data.partition(":")
    message = query.message
    where = locate(message, query.from_user.id)
    if kind == "a":
        await _on_answer(query, deps, value)
        return
    if await deps.store.get_link(where.user_id) is None:
        await query.answer("Link your account first.", show_alert=True)
        return
    session = await _session(deps, where)
    try:
        if kind == "m":
            models = await deps.llm.models(await linking.api_key_for(deps.store, deps.vault, where.user_id) or "")
            reply = commands.set_model(session, models[int(value)])
        elif kind == "r":
            reply = commands.set_reasoning(session, value)
        else:
            commands.reset_settings(session)
            reply = "Settings reset."
    except (ValueError, IndexError, EndpointError) as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await deps.store.save_session(session)
    await query.answer(reply)
    await deps.sender.edit(message.chat.id, message.message_id, reply)


async def _on_answer(query, deps: Deps, value: str) -> None:
    message = query.message
    session = await deps.store.find_pending(message.chat.id, message.message_id)
    if session is None or session.pending is None:
        await query.answer("This question is no longer open.", show_alert=True)
        return
    if query.from_user.id != session.telegram_user_id:
        await query.answer("This question is not yours.", show_alert=True)
        return
    if value == "o":
        await query.answer("Send your answer as a message.")
        return
    pending = session.pending
    try:
        label = pending.options[int(value)]
    except (ValueError, IndexError):
        await query.answer("Unknown choice.", show_alert=True)
        return
    await query.answer()
    await deps.sender.edit(message.chat.id, message.message_id, f"{pending.question}\n\n✅ {label}")
    where = locate(message, query.from_user.id)
    target = Target(message.chat.id, message.chat.type, where.target.thread_id, None)
    deps.runtime.submit(Job(target, query.from_user.id, session.key, content.Item(label), batching.text_delay(0)))


# -- plumbing ----------------------------------------------------------------------------------
class _Seen:
    def __init__(self) -> None:
        self._ids: OrderedDict[int, None] = OrderedDict()

    def add(self, update_id: int) -> bool:
        """True when the id is new."""
        if update_id in self._ids:
            return False
        self._ids[update_id] = None
        if len(self._ids) > SEEN_UPDATES:
            self._ids.popitem(last=False)
        return True


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        log.error("Telegram reports another bot instance is polling this token; stop the other one.")
    else:
        log.error("unhandled error", exc_info=context.error)


def register(app: Application) -> None:
    seen = _Seen()

    async def dedupe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not seen.add(update.update_id):
            raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, dedupe), group=-1)
    for name, fn in (
        ("start", cmd_start), ("new", cmd_new), ("stop", cmd_stop), ("whoami", cmd_whoami), ("help", cmd_help),
        ("model", cmd_model), ("reasoning", cmd_reasoning), ("set", cmd_set), ("unset", cmd_unset), ("settings", cmd_settings),
    ):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^[amrs]:"))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
