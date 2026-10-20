"""What the model is told about a message: its text, quoted reply, forward origin and images."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

IMAGE_PART = "image_url"


@dataclass(frozen=True)
class Item:
    """One incoming message, ready to join a turn."""

    text: str = ""
    images: tuple[str, ...] = field(default_factory=tuple)  # data: URLs


def reply_prefix(quoted: str | None) -> str:
    return f'[Replying to: "{quoted.strip()}"]\n\n' if quoted and quoted.strip() else ""


def forward_prefix(origin: str | None, when: datetime | None) -> str:
    name = origin or "an unknown sender"
    stamp = f", {when.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC" if when else ""
    return f"[Forwarded from {name}{stamp}]\n"


def compose(text: str, *, quoted: str | None = None, forwarded: tuple[str | None, datetime | None] | None = None) -> str:
    head = ""
    if forwarded is not None:
        head += forward_prefix(*forwarded)
    return reply_prefix(quoted) + head + text


def merge(items: list[Item]) -> str | list[dict[str, Any]]:
    """One user message content from several items: a string, or parts when there are images."""
    text = "\n".join(i.text for i in items if i.text)
    images = [url for i in items for url in i.images]
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    parts += [{"type": IMAGE_PART, "image_url": {"url": url}} for url in images]
    return parts


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def drop_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Earlier turns without their images: the text, which is what identifies a thread, is kept."""
    out = copy.deepcopy(messages)
    for m in out:
        content = m.get("content")
        if isinstance(content, list):
            texts = [p for p in content if not (isinstance(p, dict) and p.get("type") == IMAGE_PART)]
            m["content"] = text_of(texts) if all(p.get("type") == "text" for p in texts) else texts
    return out
