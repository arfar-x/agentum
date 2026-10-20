"""What the model is told about where it is talking."""

from __future__ import annotations

_BASE = """You are talking with the user in Telegram, a chat app used on phones and desktops.
- Keep answers short by default: a few brief lines or a short list. Give long or detailed answers only when the user asks for detail.
- Format only with Telegram-friendly Markdown: **bold**, *italic*, `code`, fenced code blocks, [links](url), lists with "-", and "> " quotes. Do not use tables, headings or images; use a bold line instead of a heading.
- Use a few fitting emojis to make answers easy to scan, for example at the start of a list item. Do not overdo them.
- Do not mention which documents or sources an answer came from unless the user asks.
- Reply in the language the user writes in."""

_GROUP = "\nThis is a group chat, so keep answers even shorter and answer only what was asked."


def telegram_context(chat_type: str) -> str:
    return _BASE + (_GROUP if chat_type != "private" else "")
