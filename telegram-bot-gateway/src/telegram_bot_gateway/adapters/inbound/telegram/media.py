"""What a Telegram message carries besides text, as the model can take it."""

from __future__ import annotations

import base64
import mimetypes
from typing import Any

from telegram import Bot, Message

from telegram_bot_gateway.domain.content import Item

TEXT_DOCUMENT_LIMIT = 100_000
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log", ".py", ".js", ".ts", ".sql", ".xml", ".html", ".sh", ".toml", ".ini"}


class DownloadError(Exception):
    pass


async def _download(bot: Bot, file_id: str) -> bytes:
    try:
        return bytes(await (await bot.get_file(file_id)).download_as_bytearray())
    except Exception as exc:
        raise DownloadError(str(exc)) from exc


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _is_text(name: str, mime: str | None) -> bool:
    guessed = mime or mimetypes.guess_type(name)[0] or ""
    return guessed.startswith("text/") or any(name.lower().endswith(s) for s in TEXT_SUFFIXES)


def has_voice(message: Message) -> bool:
    return bool(message.voice or message.audio or message.video_note)


async def media_item(message: Message, bot: Bot) -> Item:
    """Images and the text stand-ins for everything else the message carries (not its own text)."""
    texts: list[str] = []
    images: list[str] = []
    if message.photo:
        images.append(_data_url(await _download(bot, message.photo[-1].file_id), "image/jpeg"))
    doc = message.document
    if doc is not None:
        name, mime = doc.file_name or "file", doc.mime_type
        if mime and mime.startswith("image/"):
            images.append(_data_url(await _download(bot, doc.file_id), mime))
        elif _is_text(name, mime) and (doc.file_size or 0) <= TEXT_DOCUMENT_LIMIT:
            body = (await _download(bot, doc.file_id)).decode("utf-8", errors="replace")
            texts.append(f"[Content of {name}]:\n{body}")
        else:
            texts.append(f"[Document: {name}, {doc.file_size or '?'} bytes, {mime or 'unknown type'}; its content is not available]")
    if message.sticker:
        texts.append(f"[The user sent a sticker {message.sticker.emoji or ''}]".replace(" ]", "]"))
    if message.venue:
        loc = message.venue.location
        texts.append(f"[Venue: {message.venue.title}, {message.venue.address} ({loc.latitude}, {loc.longitude}) "
                     f"https://maps.google.com/?q={loc.latitude},{loc.longitude}]")
    elif message.location:
        loc = message.location
        texts.append(f"[Location: {loc.latitude}, {loc.longitude} https://maps.google.com/?q={loc.latitude},{loc.longitude}]")
    if message.video or message.animation:
        texts.append("[The user sent a video, which this bot cannot read]")
    return Item("\n".join(texts), tuple(images))


def forward_origin(message: Message) -> tuple[str | None, Any] | None:
    origin = message.forward_origin
    if origin is None:
        return None
    name = None
    if getattr(origin, "sender_user", None) is not None:
        name = origin.sender_user.full_name
    elif getattr(origin, "sender_user_name", None):
        name = origin.sender_user_name
    elif getattr(origin, "sender_chat", None) is not None:
        name = origin.sender_chat.title
    elif getattr(origin, "chat", None) is not None:
        name = origin.chat.title
    return name, origin.date
