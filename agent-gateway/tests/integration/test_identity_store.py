# Covers: FR-IDN-01, FR-IDN-05, FR-IDN-07, FR-KEY-01, FR-KEY-02, FR-KEY-03, FR-KEY-04, FR-KEY-05, FR-KEY-06
# Covers: FR-KEY-07, FR-KEY-08, FR-CRD-06, FR-CRD-07, FR-CRD-09, FR-AUD-01, FR-AUD-02, FR-AUD-03, FR-AUD-04
# Covers: FR-AUD-05, FR-AUD-06, FR-OPS-04, FR-OPS-05, FR-OPS-06, FR-OPS-08, FR-AUT-02, FR-AUT-03, FR-CRD-03

import io
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.errors import UniqueViolation

from agent_gateway.adapters.inbound.cli import parser, run
from agent_gateway.adapters.outbound.crypto import Vault
from agent_gateway.adapters.outbound.postgres_store import PostgresStore
from agent_gateway.application.identity_ports import Account, NewKey
from agent_gateway.application.key_service import KeyManager
from agent_gateway.application.models import PendingTurn, Thread
from agent_gateway.domain.identity import KeyIssuingDisabled, KeyScope, hash_key
from agent_gateway.domain.messages import ChatMessage, FunctionCall, ToolCall

pytestmark = pytest.mark.services

DSN = os.environ.get("GATEWAY_TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
MODEL_KEY = "sk-live-INTEGRATION-0123456789-WXYZ"


def now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def store():
    if not DSN:
        pytest.skip("no test database (run scripts/test-services.sh)")
    s = await PostgresStore.open(DSN, Vault(Vault.generate_key()), MIGRATIONS)
    async with s._pool.connection() as c:
        await c.execute("TRUNCATE api_keys, user_credentials, threads, pending_turns, responses, "
                        "audit_log, directory_snapshot CASCADE")
    yield s
    await s.close()


def keys_of(store, issue=True) -> KeyManager:
    return KeyManager(store, store.box, issue_keys=issue, now=now)


async def rows(store, sql, *args):
    async with store._pool.connection() as c:
        return await (await c.execute(sql, args)).fetchall()


async def test_migration_005_applies_on_a_database_that_holds_the_001_to_004_schema(tmp_path):
    # Covers: FR-IDN-07
    if not DSN:
        pytest.skip("no test database")
    name = "agw_mig_" + uuid.uuid4().hex[:8]
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as admin:
        await admin.execute(f'CREATE DATABASE "{name}"')
    dsn = make_conninfo(DSN, dbname=name)
    old = tmp_path / "old"
    old.mkdir()
    for f in sorted(MIGRATIONS.glob("00[1-4]_*.sql")):
        shutil.copy(f, old / f.name)
    vault = Vault(Vault.generate_key())
    try:
        s = await PostgresStore.open(dsn, vault, old)
        async with s._pool.connection() as c:                       # rows written under free-text user ids
            await c.execute("INSERT INTO api_keys (user_id, label, key_hash) VALUES ('alice@example.org','','h')")
            await c.execute("INSERT INTO user_credentials (user_id, server, var, ciphertext) VALUES ('alice@example.org','s','V','x')")
            await c.execute("INSERT INTO threads (user_id, history_hash, active_agent) VALUES ('alice@example.org','h','a')")
            await c.execute("INSERT INTO responses (user_id, id, payload) VALUES ('alice@example.org','r','x')")
        await s.put_pending(PendingTurn(
            user_id="alice@example.org", call_ids=["c"], kind="approval", agent_id="a",
            queue=[ToolCall(id="w", function=FunctionCall(name="t", arguments="{}"))], hidden_messages=[ChatMessage(role="user", content="x")],
            code_hash="h", expires_at=2000.0))
        await s.migrate(MIGRATIONS)
        for table in ("api_keys", "user_credentials", "threads", "responses", "pending_turns", "pending_calls"):
            assert await rows(s, f"SELECT 1 FROM {table}") == [], table
        cols = {t: [r[0] for r in await rows(s, "SELECT column_name FROM information_schema.columns "
                                                "WHERE table_name=%s ORDER BY ordinal_position", t)]
                for t in ("api_keys", "user_credentials", "directory_snapshot", "audit_log")}
        assert cols["api_keys"] == ["id", "owner", "kind", "label", "prefix", "key_hash", "secret_enc", "scope",
                                    "created_at", "expires_at", "revoked_at", "last_used_at"]
        assert cols["user_credentials"] == ["user_id", "server", "var", "ciphertext", "updated_at"]
        assert cols["directory_snapshot"] == ["user_id", "username", "kind", "groups", "roles", "enabled", "synced_at"]
        assert cols["audit_log"] == ["id", "ts", "last_ts", "actor", "action", "target", "detail", "count"]
        await s.migrate(MIGRATIONS)                                  # applied once: a second run changes nothing
        await s.put_thread(Thread("sub-1", "h", "a"))
        await s.migrate(MIGRATIONS)
        assert (await s.get_thread("sub-1", "h")) is not None
        await s.close()
    finally:
        async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as admin:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def test_table_constraints_keep_secrets_where_the_data_model_says(store):
    keys = keys_of(store)
    _, issued = await keys.issue("alice", "", KeyScope(), None)
    registered = await keys.register_upstream("bob", "", MODEL_KEY)
    kinds = await rows(store, "SELECT kind, secret_enc IS NULL FROM api_keys ORDER BY kind")
    assert kinds == [("issued", True), ("upstream", False)]
    async with store._pool.connection() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            await c.execute("UPDATE api_keys SET secret_enc='x' WHERE id=%s::uuid", (issued.id,))
    async with store._pool.connection() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            await c.execute("UPDATE api_keys SET revoked_at=now() WHERE id=%s::uuid", (registered.id,))   # secret not wiped


async def test_a_model_key_is_hashed_and_encrypted_and_one_person_has_one_active_key(store):
    # Covers: FR-KEY-01, FR-KEY-02
    keys = keys_of(store)
    first = await keys.register_upstream("alice", "old", MODEL_KEY)
    (raw,) = (await rows(store, "SELECT secret_enc, key_hash FROM api_keys"))
    assert MODEL_KEY.encode() not in bytes(raw[0]) and raw[1] == hash_key(MODEL_KEY)
    assert await keys.upstream_key("alice") == MODEL_KEY
    second = await keys.register_upstream("alice", "new", MODEL_KEY + "-2")
    assert second.prefix == (MODEL_KEY + "-2")[-4:]
    old = await rows(store, "SELECT revoked_at IS NOT NULL, secret_enc IS NULL FROM api_keys WHERE id=%s::uuid", first.id)
    assert old == [(True, True)]
    assert await keys.upstream_key("alice") == MODEL_KEY + "-2"
    assert len([k for k in await keys.list_for("alice") if k.revoked_at is None]) == 1
    audit = await store.audit.recent(10, action="key.register")
    assert audit[0].detail["replaced"] == first.id


async def test_a_model_key_may_be_held_by_several_people_but_an_issued_key_stays_unique(store):
    # Covers: FR-KEY-03
    keys = keys_of(store)
    a = await keys.register_upstream("alice", "", MODEL_KEY)
    b = await keys.register_upstream("bob", "", MODEL_KEY)                   # no refusal
    assert {i.owner for i in await keys.resolve_all(MODEL_KEY)} == {"alice", "bob"}
    assert await keys.upstream_key("alice") == await keys.upstream_key("bob") == MODEL_KEY
    await keys.revoke("alice", a.id)
    assert [i.id for i in await keys.resolve_all(MODEL_KEY)] == [b.id]
    assert await keys.upstream_key("alice") is None and await keys.upstream_key("bob") == MODEL_KEY
    secret, issued = await keys.issue("alice", "", KeyScope(), None)
    with pytest.raises(UniqueViolation):                                     # the index still guards issued keys
        await store.add_key(NewKey("bob", "issued", "", "agk_", hash_key(secret), None, KeyScope(), None), actor="bob")


async def test_issuing_needs_the_flag_and_an_issued_key_authenticates_only_while_it_is_on(store):
    # Covers: FR-KEY-04, FR-KEY-05
    with pytest.raises(KeyIssuingDisabled):
        await keys_of(store, issue=False).issue("alice", "", KeyScope(), None)
    assert await rows(store, "SELECT 1 FROM api_keys") == []
    on = keys_of(store)
    secret, info = await on.issue("alice", "ci", KeyScope(("tasks",), False), now() + timedelta(days=1))
    assert secret.startswith("agk_") and info.expires_at and info.scope == KeyScope(("tasks",), False)
    (stored,) = await rows(store, "SELECT key_hash, secret_enc, scope FROM api_keys")
    assert stored[0] == hash_key(secret) and secret not in stored[0] and stored[1] is None
    assert stored[2] == {"agents": ["tasks"], "write": False}
    assert [i.owner for i in await on.resolve_all(secret)] == ["alice"]
    assert await keys_of(store, issue=False).resolve_all(secret) == []


async def test_keys_resolve_until_revoked_or_expired_and_revocation_is_owner_scoped(store):
    # Covers: FR-KEY-06, FR-KEY-07, FR-AUT-02, FR-AUT-03
    keys = keys_of(store)
    secret, info = await keys.issue("alice", "laptop", KeyScope(), None)
    short, _ = await keys.issue("alice", "short", KeyScope(), now() + timedelta(milliseconds=1))
    async with store._pool.connection() as c:
        await c.execute("UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE label='short'")
    assert await keys.resolve_all(short) == []
    listing = await keys.list_for("alice")
    assert {k.label for k in listing} == {"laptop", "short"} and secret not in repr(listing)
    assert not await keys.revoke("bob", info.id)
    assert await keys.resolve_all(secret) != []
    assert await keys.revoke("alice", info.id)
    assert await keys.resolve_all(secret) == []
    assert not await keys.revoke(None, info.id)
    assert (await store.audit.recent(1, action="key.revoke"))[0].detail == {"by": "owner"}


async def test_last_used_moves_at_most_once_a_minute(store):
    # Covers: FR-KEY-08
    keys = keys_of(store)
    secret, info = await keys.issue("alice", "", KeyScope(), None)
    await keys.resolve_all(secret)
    (first,) = (await rows(store, "SELECT last_used_at FROM api_keys"))[0]
    assert first is not None
    await keys.resolve_all(secret)
    assert (await rows(store, "SELECT last_used_at FROM api_keys"))[0][0] == first
    async with store._pool.connection() as c:
        await c.execute("UPDATE api_keys SET last_used_at = last_used_at - interval '2 minutes'")
    await keys.resolve_all(secret)
    assert (await rows(store, "SELECT last_used_at FROM api_keys"))[0][0] > first - timedelta(minutes=2)


async def test_stored_variable_names_are_listed_and_one_can_go_without_the_others_and_values_stay_out_of_audit(store):
    # Covers: FR-CRD-06, FR-CRD-07, FR-CRD-03
    await store.set_credential("alice", "skills", "JIRA_USER", "alice-jira", actor="alice")
    await store.set_credential("alice", "skills", "JIRA_PASSWORD", "hunter2-SENTINEL", actor="alice")
    await store.set_credential("bob", "skills", "JIRA_USER", "bob-jira")
    assert await store.list_vars("alice", "skills") == ["JIRA_PASSWORD", "JIRA_USER"]
    assert await store.list_vars("alice", "other") == [] and await store.list_vars("carol", "skills") == []
    assert await store.delete_var("alice", "skills", "JIRA_PASSWORD", actor="alice")
    assert not await store.delete_var("alice", "skills", "JIRA_PASSWORD")
    assert await store.get("alice", "skills") == {"JIRA_USER": "alice-jira"}
    assert await store.get("bob", "skills") == {"JIRA_USER": "bob-jira"}
    events = await store.audit.recent(20)
    assert {(e.action, e.target) for e in events} >= {("cred.set", "skills/JIRA_PASSWORD"), ("cred.delete", "skills/JIRA_PASSWORD")}
    assert "hunter2" not in json.dumps([e.detail for e in events]) + "".join(e.target for e in events)


async def test_audit_entries_fold_within_a_minute_are_bounded_and_pruned(store):
    # Covers: FR-AUD-01, FR-AUD-05, FR-AUD-06
    audit = store.audit
    await audit.record("alice", "auth.denied", "unknown_key", {"path": "/v1/models"})
    await audit.record("alice", "auth.denied", "unknown_key", {"path": "/v1/models"})
    await audit.record("alice", "auth.denied", "expired")
    (folded,) = [e for e in await audit.recent(10) if e.target == "unknown_key"]
    assert folded.count == 2 and folded.last_ts >= folded.ts and folded.detail == {"path": "/v1/models"}
    async with store._pool.connection() as c:
        await c.execute("UPDATE audit_log SET last_ts = last_ts - interval '61 seconds' WHERE target='unknown_key'")
    await audit.record("alice", "auth.denied", "unknown_key")
    assert sorted(e.count for e in await audit.recent(10) if e.target == "unknown_key") == [1, 2]
    await audit.record("system", "directory.sync_failed", "", {"error": "e" * 9000})
    big = (await audit.recent(1, action="directory.sync_failed"))[0]
    assert len(json.dumps(big.detail).encode()) <= 2100
    assert len(await audit.recent(10, actor="system")) == 1
    assert len(await audit.recent(10, since=now() + timedelta(hours=1))) == 0
    cutoff = now() + timedelta(seconds=1)
    assert await audit.prune(cutoff) == 4
    assert await audit.recent(10) == []


async def test_a_change_and_its_audit_entry_succeed_or_fail_together_and_nothing_secret_is_logged(store):
    # Covers: FR-AUD-02, FR-AUD-03, FR-AUD-04
    keys = keys_of(store)
    await keys.register_upstream("alice", "", MODEL_KEY)
    _, issued = await keys.issue("alice", "ci", KeyScope(), None, actor="cli")
    await keys.revoke(None, issued.id, actor="cli")
    await store.set_credential("alice", "skills", "JIRA_PASSWORD", "hunter2-SENTINEL")
    await store.delete_credentials("alice", "skills")
    await store.purge("alice")
    events = await store.audit.recent(20)
    assert {e.action for e in events} == {"key.register", "key.issue", "key.revoke", "cred.set", "cred.delete", "owner.purge"}
    dump = json.dumps([(e.actor, e.action, e.target, e.detail) for e in events])
    assert MODEL_KEY not in dump and "hunter2" not in dump
    before = len(await store.audit.recent(100))
    a = keys_of(store)
    await a.register_upstream("a", "", "same-key-1234")
    await a.register_upstream("b", "", "same-key-1234")
    assert len(await store.audit.recent(100)) == before + 2


async def test_the_directory_copy_is_replaced_in_one_transaction_and_a_failure_changes_nothing(store):
    # Covers: FR-IDN-05
    d = store.directory
    t1 = now()
    await d.replace_all([
        Account("s1", "alice", True, ("product", "bad,name"), ("agent-user",)),
        Account("s2", "service-account-bot", True, (), (), "service"),
        Account("s3", "carol", False, ("ops",), ())], t1)
    alice = await d.lookup("s1")
    assert (alice.groups, alice.roles, alice.enabled, alice.kind, alice.synced_at) == (
        ("product",), ("agent-user",), True, "user", t1)
    assert (await d.lookup("s2")).kind == "service" and (await d.lookup("s3")).enabled is False
    assert await d.newest_sync() == t1
    with pytest.raises(Exception):
        await d.replace_all([Account("s1", "alice-renamed", True, (), ()), Account("s4", None, True, (), ())], now())  # type: ignore[arg-type]
    assert (await d.lookup("s1")).username == "alice" and await d.lookup("s4") is None
    assert await d.newest_sync() == t1
    t2 = now()
    await d.replace_all([Account("s1", "alice", True, ("product",), ())], t2)
    assert await d.lookup("s2") is None and await d.lookup("s3") is None and await d.newest_sync() == t2
    cols = [r[0] for r in await rows(store, "SELECT column_name FROM information_schema.columns WHERE table_name='directory_snapshot'")]
    assert not {"email", "attributes", "name"} & set(cols)


async def test_purging_an_owner_deletes_everything_stored_for_them_and_only_them(store):
    # Covers: FR-OPS-08, FR-IDN-01
    keys = keys_of(store)
    await keys.register_upstream("alice", "", MODEL_KEY)
    await keys.register_upstream("bob", "", "sk-bob-0000")
    await store.set_credential("alice", "skills", "V", "x")
    await store.put_thread(Thread("alice", "h", "a"))
    await store.put_response("alice", "r1", [ChatMessage(role="user", content="x")])
    call = ToolCall(id="w", function=FunctionCall(name="t", arguments="{}"))
    await store.put_pending(PendingTurn(user_id="alice", call_ids=["c1"], kind="approval", agent_id="a", queue=[call],
                                        hidden_messages=[ChatMessage(role="user", content="x")], code_hash="h", expires_at=2000.0))
    await store.put_thread(Thread("bob", "h", "a"))
    counts = await store.purge("alice")
    assert counts == {"keys": 1, "credentials": 1, "threads": 1, "pending_turns": 1, "responses": 1}
    assert await keys.list_for("alice") == [] and await store.get("alice", "skills") == {}
    assert await store.get_thread("alice", "h") is None and await store.get_response("alice", "r1") is None
    assert await store.take_pending("alice", "c1", 1000.0) is None
    assert await keys.upstream_key("bob") == "sk-bob-0000" and await store.get_thread("bob", "h") is not None
    purge = (await store.audit.recent(1, action="owner.purge"))[0]
    assert (purge.actor, purge.target, purge.detail) == ("cli", "alice", {"counts": counts})


class Keep:
    """Lets the CLI close its store without closing the shared test one."""

    def __init__(self, store):
        self._s = store

    def __getattr__(self, name):
        return getattr(self._s, name)

    async def close(self):
        pass


async def cli(store, *argv, stdin="", issue_keys=False):
    out = io.StringIO()

    async def opener():
        return Keep(store)

    code = await run(parser().parse_args(argv), opener, out, issue_keys=issue_keys, stdin=io.StringIO(stdin))
    return code, out.getvalue()


async def test_the_cli_registers_a_model_key_from_stdin_issues_only_when_enabled_lists_and_revokes(store, capfd):
    # Covers: FR-OPS-04, FR-OPS-05
    code, text = await cli(store, "key", "register-llm", "--owner", "sub-1", "--label", "mine", stdin=MODEL_KEY + "\n")
    assert code == 0 and MODEL_KEY not in text and "WXYZ" in text
    capfd.readouterr()
    code, _ = await cli(store, "key", "register-llm", "--owner", "sub-2", stdin=MODEL_KEY)
    err = capfd.readouterr().err
    assert code == 0 and err == ""                       # a key held by someone else is accepted, silently
    assert (await cli(store, "key", "register-llm", "--owner", "sub-3", stdin=""))[0] == 1

    capfd.readouterr()
    code, _ = await cli(store, "key", "create", "--owner", "sub-1")
    assert code == 1 and "GATEWAY_ISSUE_KEYS" in capfd.readouterr().err
    code, text = await cli(store, "key", "create", "--owner", "sub-1", "--label", "ci", "--agents", "tasks,docs",
                           "--read-only", "--expires-days", "7", issue_keys=True)
    secret = text.splitlines()[0]
    assert code == 0 and secret.startswith("agk_")

    _, listing = await cli(store, "key", "list", "--owner", "sub-1")
    assert "upstream" in listing and "issued" in listing and "active" in listing and "tasks,docs (read-only)" in listing
    assert MODEL_KEY not in listing and secret not in listing
    assert (await cli(store, "key", "list", "--owner", "nobody"))[1] == ""

    key_id = (await keys_of(store).resolve_all(secret))[0].id
    assert (await cli(store, "key", "revoke", key_id))[0] == 0
    assert await keys_of(store).resolve_all(secret) == []
    assert (await cli(store, "key", "revoke", key_id))[0] == 1
    assert (await store.audit.recent(1, action="key.revoke"))[0].actor == "cli"


async def test_the_cli_lists_sets_and_deletes_credentials_and_shows_the_model_key_as_llm(store, monkeypatch):
    # Covers: FR-OPS-06, FR-CRD-09
    await cli(store, "key", "register-llm", "--owner", "sub-1", stdin=MODEL_KEY)
    assert (await cli(store, "creds", "set", "--owner", "sub-1", "skills", "JIRA_PASSWORD", stdin="pw-from-stdin\n"))[0] == 0
    monkeypatch.setenv("TMP_VALUE", "user-value")
    await cli(store, "creds", "set", "--owner", "sub-1", "skills", "JIRA_USER", "--value-env", "TMP_VALUE")
    assert await store.get("sub-1", "skills") == {"JIRA_PASSWORD": "pw-from-stdin", "JIRA_USER": "user-value"}
    _, listing = await cli(store, "creds", "list", "--owner", "sub-1")
    assert listing.splitlines() == ["llm  API_KEY", "skills  JIRA_PASSWORD", "skills  JIRA_USER"]
    assert "pw-from-stdin" not in listing and MODEL_KEY not in listing
    assert await rows(store, "SELECT 1 FROM user_credentials WHERE var='API_KEY'") == []       # not stored twice
    assert "deleted 1" in (await cli(store, "creds", "delete", "--owner", "sub-1", "skills", "--var", "JIRA_USER"))[1]
    assert "deleted 0" in (await cli(store, "creds", "delete", "--owner", "sub-1", "skills", "--var", "JIRA_USER"))[1]
    assert await store.list_vars("sub-1", "skills") == ["JIRA_PASSWORD"]
    assert "deleted 1" in (await cli(store, "creds", "delete", "--owner", "sub-1", "skills"))[1]
    assert (await cli(store, "creds", "list", "--owner", "sub-1"))[1] == "llm  API_KEY\n"


async def test_the_cli_lists_audit_events_with_filters_and_purges_only_when_confirmed(store, capfd):
    # Covers: FR-AUD-01, FR-OPS-08
    await cli(store, "key", "register-llm", "--owner", "sub-1", stdin=MODEL_KEY)
    await cli(store, "creds", "set", "--owner", "sub-1", "skills", "V", stdin="x")
    _, everything = await cli(store, "audit", "list")
    assert "key.register" in everything and "cred.set" in everything and MODEL_KEY not in everything
    _, only = await cli(store, "audit", "list", "--action", "cred.set", "--actor", "cli", "--limit", "5")
    assert "cred.set" in only and "key.register" not in only
    assert (await cli(store, "audit", "list", "--since", "1h"))[1] != ""
    assert (await cli(store, "audit", "list", "--actor", "nobody"))[1] == ""
    capfd.readouterr()
    code, _ = await cli(store, "owner", "purge", "--owner", "sub-1")
    assert code == 1 and "--yes" in capfd.readouterr().err and await store.list_vars("sub-1", "skills") == ["V"]
    code, text = await cli(store, "owner", "purge", "--owner", "sub-1", "--yes")
    assert code == 0 and "1 keys" in text and "1 credentials" in text
    assert await store.list_vars("sub-1", "skills") == []
    assert "owner.purge" in (await cli(store, "audit", "list", "--action", "owner.purge"))[1]
