"""Which conversation a message belongs to."""

from __future__ import annotations

GENERAL_TOPIC = 1


def effective_thread_id(*, thread_id: int | None, is_topic_message: bool, is_forum: bool) -> int | None:
    """Keep a thread id only for real topic messages.

    A reply outside a topic carries the replied message's id as its thread id, which Telegram
    refuses as a destination; a forum's General topic arrives with none and is topic 1.
    """
    if is_topic_message and thread_id is not None:
        return thread_id
    if is_forum and thread_id is None:
        return GENERAL_TOPIC
    return None


def session_key(*, chat_type: str, chat_id: int, thread_id: int | None, user_id: int) -> str:
    base = f"{chat_id}:{thread_id}" if thread_id is not None else str(chat_id)
    return base if chat_type == "private" else f"{base}:{user_id}"
