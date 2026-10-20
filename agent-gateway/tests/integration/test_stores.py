# Covers: FR-MEM-03, FR-OPS-04, FR-AUT-02, FR-AUT-03, FR-AUT-04, FR-CRD-02, FR-CRD-03, FR-OPS-01, FR-OPS-02, FR-OPS-03

import asyncio
import os
from pathlib import Path

import pytest
import redis.asyncio as aioredis

from agent_gateway.adapters.outbound.crypto import Vault, hash_key
from agent_gateway.adapters.outbound.postgres_store import PostgresStore
from agent_gateway.adapters.outbound.redis_locks import LockTimeout, RedisLock, RedisRateLimiter
from agent_gateway.application.models import PendingTurn, Thread
from agent_gateway.domain.messages import ChatMessage, FunctionCall, ToolCall

pytestmark = pytest.mark.services

DSN, REDIS = os.environ.get("GATEWAY_TEST_DATABASE_URL"), os.environ.get("GATEWAY_TEST_REDIS_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
async def store():
    if not DSN:
        pytest.skip("no test database (run scripts/test-services.sh)")
    s = await PostgresStore.open(DSN, Vault(Vault.generate_key()), MIGRATIONS)
    async with s._pool.connection() as c:
        await c.execute("TRUNCATE api_keys, user_credentials, threads, pending_turns CASCADE")
    yield s
    await s.close()


def pending(user="u1", **kw):
    call = ToolCall(id="w1", function=FunctionCall(name="t", arguments="{}"))
    base = dict(user_id=user, call_ids=["c1"], kind="approval", agent_id="a", queue=[call], approval_call=call,
                hidden_messages=[ChatMessage(role="assistant", content="secret jira text", tool_calls=[call])],
                code_hash="h", expires_at=2000.0)
    base.update(kw)
    return PendingTurn(**base)


async def test_keys_are_hashed_shown_once_and_revocation_is_immediate(store):
    secret = await store.create_key("alice", "laptop")
    assert await store.find_user(hash_key("wrong")) is None
    key_id, user = await store.find_user(hash_key(secret))
    assert user == "alice"
    async with store._pool.connection() as c:
        row = await (await c.execute("SELECT key_hash FROM api_keys")).fetchone()
    assert secret not in row[0] and row[0] == hash_key(secret)
    assert await store.revoke_key(key_id)
    assert await store.find_user(hash_key(secret)) is None


async def test_credentials_are_encrypted_at_rest_and_scoped_to_their_owner(store):
    await store.set_credential("alice", "skills", "JIRA_PASSWORD", "hunter2")
    assert await store.get("alice", "skills") == {"JIRA_PASSWORD": "hunter2"}
    assert await store.get("bob", "skills") == {}
    async with store._pool.connection() as c:
        raw = bytes((await (await c.execute("SELECT ciphertext FROM user_credentials")).fetchone())[0])
    assert b"hunter2" not in raw
    assert await store.delete_credentials("alice", "skills") == 1


async def test_paused_turns_round_trip_encrypted_and_resume_once_for_their_owner_only(store):
    await store.put_pending(pending())
    async with store._pool.connection() as c:
        raw = bytes((await (await c.execute("SELECT payload FROM pending_turns")).fetchone())[0])
    assert b"secret jira text" not in raw
    assert await store.take_pending("u2", "c1", 1000.0) is None
    got = await store.take_pending("u1", "c1", 1000.0)
    assert got.hidden_messages[0].content == "secret jira text" and got.approval_call.id == "w1"
    assert await store.take_pending("u1", "c1", 1000.0) is None          # single use


async def test_code_lookup_expiry_and_pruning(store):
    await store.put_pending(pending(call_ids=[], code_hash="abc"))
    assert await store.take_pending_by_code("u1", "abc", 3000.0) is None     # expired
    await store.put_pending(pending(call_ids=[], code_hash="abc"))
    assert await store.take_pending_by_code("u2", "abc", 1000.0) is None     # foreign
    assert await store.take_pending_by_code("u1", "abc", 1000.0) is not None
    await store.put_pending(pending(call_ids=["z"]))
    await store.prune(5000.0, 30)
    assert await store.take_pending("u1", "z", 1000.0) is None


async def test_threads_survive_a_reconnect_and_are_user_scoped(store):
    await store.put_thread(Thread("u1", "h1", "tasks", True))
    await store.close()
    again = await PostgresStore.open(DSN, store._vault, MIGRATIONS)
    try:
        assert (await again.get_thread("u1", "h1")).active_agent == "tasks"
        assert (await again.get_thread("u1", "h1")).client_opt_in is True
        assert await again.get_thread("u2", "h1") is None
    finally:
        await again.close()


async def test_a_threads_trace_is_stored_encrypted_and_read_back(store):
    trace = [{"agent": "a", "tool": "kb_search", "args": {"queries": ["distinctive-phrase"]}, "ok": True}]
    await store.put_thread(Thread("u1", "h2", "tasks", False, trace))
    assert (await store.get_thread("u1", "h2")).trace == trace
    assert (await store.get_thread("u2", "h2")) is None
    async with store._pool.connection() as c:
        raw = (await (await c.execute("SELECT trace FROM threads WHERE history_hash='h2'")).fetchone())[0]
    assert b"distinctive-phrase" not in bytes(raw) and b"kb_search" not in bytes(raw)
    await store.put_thread(Thread("u1", "h3", "tasks"))
    assert (await store.get_thread("u1", "h3")).trace == []


async def test_the_redis_lock_serializes_and_the_rate_limit_counts():
    if not REDIS:
        pytest.skip("no test redis")
    r = aioredis.from_url(REDIS)
    await r.flushdb()
    order = []
    lock = RedisLock(r, wait_seconds=5)

    async def worker(name):
        async with lock.hold("conv"):
            order.append(f"{name}-in")
            await asyncio.sleep(0.1)
            order.append(f"{name}-out")

    await asyncio.gather(worker("a"), worker("b"))
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])
    with pytest.raises(LockTimeout):
        async with lock.hold("x"):
            async with RedisLock(r, wait_seconds=0.2).hold("x"):
                pass
    limiter = RedisRateLimiter(r)
    assert [await limiter.allow("k", 2) for _ in range(3)] == [True, True, False]
    await r.aclose()


async def test_the_cli_creates_lists_and_revokes_keys_and_manages_credentials(store, monkeypatch, capsys):
    # Covers: FR-OPS-04
    import io

    from agent_gateway.adapters.inbound.cli import parser, run

    class Keep:
        """Lets the CLI close its store without closing the shared test one."""

        def __getattr__(self, name):
            return getattr(store, name)

        async def close(self):
            pass

    async def opener():
        return Keep()

    async def cli(*argv):
        out = io.StringIO()
        code = await run(parser().parse_args(argv), opener, out)
        return code, out.getvalue()

    code, text = await cli("key", "create", "carol", "--label", "laptop")
    secret = text.splitlines()[0]
    assert code == 0 and secret.startswith("agw_")
    assert await store.find_user(hash_key(secret)) is not None
    _, listing = await cli("key", "list")
    assert "carol" in listing and "active" in listing
    key_id = (await store.find_user(hash_key(secret)))[0]
    assert (await cli("key", "revoke", key_id))[0] == 0
    assert await store.find_user(hash_key(secret)) is None
    assert (await cli("key", "revoke", key_id))[0] == 1
    monkeypatch.setenv("TMP_VALUE", "pw")
    await cli("creds", "set", "carol", "skills", "JIRA_PASSWORD", "--value-env", "TMP_VALUE")
    assert await store.get("carol", "skills") == {"JIRA_PASSWORD": "pw"}
    assert "deleted 1" in (await cli("creds", "delete", "carol", "skills"))[1]


async def test_the_text_cache_expires_and_renews_on_use():
    # Covers: FR-AGT-08
    if not REDIS:
        pytest.skip("no test redis")
    from agent_gateway.adapters.outbound.redis_locks import RedisTextCache

    r = aioredis.from_url(REDIS)
    await r.flushdb()
    cache = RedisTextCache(r)
    assert await cache.get("k", 60) is None
    await cache.put("k", "condensed", 2)
    assert await cache.get("k", 600) == "condensed"          # a hit renews the life
    assert 2 < await r.ttl("k") <= 600
    # a newer entry in the same group displaces the older one
    await cache.put("a", "first", 600, "user1", 1)
    await cache.put("b", "second", 600, "user1", 1)
    assert await cache.get("a", 600, "user1") is None and await cache.get("b", 600, "user1") == "second"
    await cache.put("c", "other user", 600, "user2", 1)
    assert await cache.get("b", 600, "user1") == "second"
    await r.aclose()
