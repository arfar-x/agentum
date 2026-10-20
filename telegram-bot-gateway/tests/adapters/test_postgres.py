import os
from pathlib import Path

import pytest

from telegram_bot_gateway.adapters.outbound.postgres import PostgresStore
from telegram_bot_gateway.application.model import Link, PairingRequest, Pending, Session

# Covers: FR-OPS-04, FR-SES-02, FR-SES-04, FR-SES-05, FR-ASK-07, FR-LNK-05

pytestmark = pytest.mark.services
URL = os.environ.get("BOT_TEST_DATABASE_URL", "")
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
async def store():
    if not URL:
        pytest.skip("BOT_TEST_DATABASE_URL not set (scripts/test-services.sh)")
    s = await PostgresStore.open(URL, MIGRATIONS)
    for table in ("links", "pairing_codes", "sessions", "message_texts"):
        await s._run(f"DELETE FROM {table}")
    yield s
    await s.close()


async def test_migrations_apply_once_and_reopening_is_harmless(store):
    again = await PostgresStore.open(URL, MIGRATIONS)
    names = [r["name"] for r in await again._rows("SELECT name FROM schema_migrations")]
    assert names == ["001_init.sql"]
    await again.close()


async def test_sessions_round_trip_exactly_including_tool_calls_and_pending(store):
    call = {"id": "c1", "type": "function", "function": {"name": "clarify", "arguments": '{"a": 1}'}}
    session = Session("-100:3:7", 7, "agent", {"temperature": 0.2},
                      [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "tool_calls": [call]}],
                      Pending("c1", "Which?", ["a", "b"], -100, 12.5, 77))
    await store.save_session(session)
    assert await store.load_session("-100:3:7") == session
    found = await store.find_pending(-100, 77)
    assert found is not None and found.key == "-100:3:7"
    assert await store.find_pending(-100, 78) is None
    session.pending = None
    await store.save_session(session)
    assert (await store.load_session("-100:3:7")).pending is None and await store.find_pending(-100, 77) is None
    await store.delete_session("-100:3:7")
    assert await store.load_session("-100:3:7") is None


async def test_links_are_stored_encrypted_text_and_removed_by_id_or_username(store):
    await store.save_link(Link(5, "ann", "Ann", "gAAAA-ciphertext"))
    await store.save_link(Link(5, "ann2", "Ann", "gAAAA-new"))  # re-linking replaces
    assert (await store.get_link(5)).api_key_enc == "gAAAA-new"
    await store.save_link(Link(6, "bob", "Bob", "x"))
    assert [l.username for l in await store.list_links()] == ["ann2", "bob"]
    assert await store.delete_links(username="bob") == 1 and await store.delete_links(telegram_user_id=5) == 1


async def test_sessions_of_a_user_are_deleted_together(store):
    for key, uid in (("5", 5), ("-1:5", 5), ("6", 6)):
        await store.save_session(Session(key, uid))
    await store.delete_sessions_of(5)
    assert await store.load_session("5") is None and await store.load_session("6") is not None


async def test_pairing_codes_and_message_texts(store):
    req = PairingRequest("ABCD2345", 5, "ann", "Ann", 55, 100.0)
    await store.create_pairing(req)
    assert await store.get_pairing("ABCD2345") == req
    assert await store.pairings_of(5) == [req] and await store.list_pairings(since=50) == [req]
    assert await store.list_pairings(since=200) == []
    await store.delete_pairing("ABCD2345")
    assert await store.get_pairing("ABCD2345") is None
    await store.save_text(1, 2, "hello")
    await store.save_text(1, 2, "hello again")
    assert await store.get_text(1, 2) == "hello again" and await store.get_text(1, 3) is None
