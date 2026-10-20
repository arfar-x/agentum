"""When the bot answers a message."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Incoming:
    chat_type: str            # private | group | supergroup
    from_bot: bool
    mentions_bot: bool
    mentions_other_bots: bool
    replies_to_bot: bool
    command_for_bot: bool = False


def is_trigger(m: Incoming) -> bool:
    if m.from_bot:
        return False
    if m.chat_type == "private":
        return True
    if m.mentions_other_bots and not m.mentions_bot:
        return False
    return m.mentions_bot or m.replies_to_bot or m.command_for_bot
