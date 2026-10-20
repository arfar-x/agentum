"""Sending to Telegram: formatting, splitting, and the fallbacks Telegram makes necessary."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from telegram import Bot, ReplyParameters
from telegram.error import BadRequest, RetryAfter

from telegram_bot_gateway.application.ports import Store
from telegram_bot_gateway.domain.formatting import Chunk, render, to_markdown_v2, to_plain

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    chat_id: int
    chat_type: str
    thread_id: int | None = None
    reply_to: int | None = None

    @property
    def replies(self) -> bool:
        """In a group forum topic the topic routes the answer; a reply there only adds noise."""
        return self.reply_to is not None and not (self.thread_id is not None and self.chat_type != "private")


def _seconds(retry_after: Any) -> float:
    return retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)


class Sender:
    def __init__(self, bot: Bot, store: Store) -> None:
        self._bot = bot
        self._store = store

    async def send(self, target: Target, text: str, *, notify: bool = True, reply_markup: Any = None) -> list[int]:
        """Send ``text`` as one or more messages; returns their ids."""
        ids: list[int] = []
        for index, chunk in enumerate(render(text)):
            base: dict[str, Any] = {"chat_id": target.chat_id, "disable_notification": not notify}
            if target.thread_id is not None:
                base["message_thread_id"] = target.thread_id
            if index == 0 and target.replies:
                base["reply_parameters"] = ReplyParameters(target.reply_to, allow_sending_without_reply=True)
            if reply_markup is not None and index == 0:
                base["reply_markup"] = reply_markup
            message = await self._with_fallbacks(self._bot.send_message, base, chunk)
            ids.append(message.message_id)
            await self._store.save_text(target.chat_id, message.message_id, chunk.plain)
        return ids

    async def edit(self, chat_id: int, message_id: int, text: str, *, reply_markup: Any = None) -> None:
        chunk = Chunk(to_markdown_v2(text), to_plain(text))
        base = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
        try:
            await self._with_fallbacks(self._bot.edit_message_text, base, chunk)
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise

    async def delete(self, chat_id: int, message_id: int) -> None:
        await self._bot.delete_message(chat_id, message_id)

    async def _with_fallbacks(self, method: Any, base: dict[str, Any], chunk: Chunk) -> Any:
        kwargs = dict(base)
        plain = False
        for _ in range(5):
            try:
                if plain:
                    return await method(text=chunk.plain, **kwargs)
                return await method(text=chunk.markdown_v2, parse_mode="MarkdownV2", **kwargs)
            except RetryAfter as exc:
                await asyncio.sleep(_seconds(exc.retry_after) + 0.5)
            except BadRequest as exc:
                reason = str(exc).lower()
                if not plain and any(w in reason for w in ("parse", "markdown", "entities")):
                    log.info("Telegram rejected the formatting; sending plain text")
                    plain = True
                elif "thread not found" in reason and kwargs.get("message_thread_id") is not None:
                    kwargs.pop("message_thread_id")
                else:
                    raise
        raise RuntimeError("Telegram kept refusing the message")
