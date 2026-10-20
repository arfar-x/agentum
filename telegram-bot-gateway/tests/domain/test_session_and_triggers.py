from telegram_bot_gateway.domain.session import effective_thread_id, session_key
from telegram_bot_gateway.domain.triggers import Incoming, is_trigger

# Covers: FR-SES-01, FR-INB-01, FR-INB-02, FR-INB-03, FR-INB-04, FR-INB-07


def test_session_keys_follow_chat_thread_and_user():
    assert session_key(chat_type="private", chat_id=5, thread_id=None, user_id=5) == "5"
    assert session_key(chat_type="private", chat_id=5, thread_id=9, user_id=5) == "5:9"
    assert session_key(chat_type="supergroup", chat_id=-100, thread_id=None, user_id=7) == "-100:7"
    assert session_key(chat_type="supergroup", chat_id=-100, thread_id=3, user_id=7) == "-100:3:7"


def test_thread_ids_only_for_real_topics():
    assert effective_thread_id(thread_id=12, is_topic_message=True, is_forum=True) == 12
    assert effective_thread_id(thread_id=12, is_topic_message=False, is_forum=False) is None  # a plain reply
    assert effective_thread_id(thread_id=None, is_topic_message=False, is_forum=True) == 1  # General
    assert effective_thread_id(thread_id=None, is_topic_message=False, is_forum=False) is None


def msg(**kw):
    base = dict(chat_type="supergroup", from_bot=False, mentions_bot=False, mentions_other_bots=False, replies_to_bot=False)
    return Incoming(**{**base, **kw})


def test_dm_always_triggers_but_never_from_a_bot():
    assert is_trigger(msg(chat_type="private"))
    assert not is_trigger(msg(chat_type="private", from_bot=True))


def test_group_needs_a_mention_reply_or_command():
    assert not is_trigger(msg())
    assert is_trigger(msg(mentions_bot=True))
    assert is_trigger(msg(replies_to_bot=True))
    assert is_trigger(msg(command_for_bot=True))


def test_group_message_for_other_bots_is_ignored():
    assert not is_trigger(msg(mentions_other_bots=True, replies_to_bot=True))
    assert is_trigger(msg(mentions_other_bots=True, mentions_bot=True))
