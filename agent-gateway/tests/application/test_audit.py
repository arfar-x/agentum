# Covers: FR-AUD-01, FR-AUD-02, FR-AUD-03, FR-AUD-05, FR-AUD-06, FR-IDN-05

import json
from datetime import timedelta

from agent_gateway.application.audit import ACTIONS, MAX_DETAIL_BYTES, clean_detail
from agent_gateway.application.identity_ports import Account
from agent_gateway.application.key_service import KeyManager
from agent_gateway.domain.identity import KeyScope
from tests.application.fakes import (
    FakeAudit,
    FakeDirectory,
    FakeKeyRecords,
    FakeNow,
    FakeSecretBox,
)

SENTINEL_KEY = "sk-SENTINEL-model-key-98765"


async def test_identical_events_within_a_minute_fold_into_one_entry_with_a_count():
    now = FakeNow()
    audit = FakeAudit(now)
    await audit.record("alice", "cred.set", "agent-skills/JIRA_PASSWORD")
    now.advance(seconds=59)
    await audit.record("alice", "cred.set", "agent-skills/JIRA_PASSWORD")
    await audit.record("alice", "cred.set", "agent-skills/OTHER")         # another target: its own entry
    await audit.record("bob", "cred.set", "agent-skills/JIRA_PASSWORD")   # another actor: its own entry
    assert [(e.actor, e.target, e.count) for e in audit.events] == [
        ("alice", "agent-skills/JIRA_PASSWORD", 2), ("alice", "agent-skills/OTHER", 1),
        ("bob", "agent-skills/JIRA_PASSWORD", 1)]
    assert audit.events[0].ts < audit.events[0].last_ts
    now.advance(seconds=61)
    await audit.record("alice", "cred.set", "agent-skills/JIRA_PASSWORD")
    assert [e.count for e in audit.events if e.actor == "alice" and e.target.endswith("PASSWORD")] == [2, 1]


async def test_detail_is_secret_free_and_bounded_to_two_kib():
    cleaned = clean_detail({"label": "x", "token": "abc", "Password": "p", "nested": {"ok": 1}})
    assert cleaned == {"label": "x", "token": "[removed]", "Password": "[removed]", "nested": {"ok": 1}}
    big = clean_detail({"error": "e" * 10_000})
    assert len(json.dumps(big).encode()) <= MAX_DETAIL_BYTES and "truncated" in big
    assert clean_detail(None) == {}


async def test_recent_filters_by_actor_action_and_time_newest_first_and_prune_drops_the_old():
    now = FakeNow()
    audit = FakeAudit(now)
    await audit.record("alice", "key.register", "k1")
    now.advance(days=2)
    await audit.record("bob", "key.revoke", "k1")
    await audit.record("alice", "key.revoke", "k2")
    assert [e.target for e in await audit.recent(10)] == ["k2", "k1", "k1"]
    assert [e.actor for e in await audit.recent(10, action="key.revoke")] == ["alice", "bob"]
    assert len(await audit.recent(10, actor="alice")) == 2
    assert len(await audit.recent(10, since=now() - timedelta(days=1))) == 2
    assert len(await audit.recent(1)) == 1
    assert await audit.prune(now() - timedelta(days=1)) == 1
    assert len(audit.events) == 2


async def test_nothing_a_key_operation_writes_to_the_audit_log_contains_a_secret():
    now = FakeNow()
    audit = FakeAudit(now)
    keys = KeyManager(FakeKeyRecords(audit, now), FakeSecretBox(), issue_keys=True, now=now)
    await keys.register_upstream("alice", "mine", SENTINEL_KEY)
    await keys.register_upstream("alice", "mine", SENTINEL_KEY + "-2")
    secret, info = await keys.issue("alice", "ci", KeyScope(("a",), False), now() + timedelta(days=1))
    await keys.revoke(None, info.id, actor="cli")
    actions = [e.action for e in audit.events]
    assert actions == ["key.register", "key.register", "key.issue", "key.revoke"]
    assert set(actions) <= ACTIONS
    dump = json.dumps([(e.actor, e.action, e.target, e.detail) for e in audit.events])
    assert SENTINEL_KEY not in dump and secret not in dump


async def test_the_directory_copy_holds_only_the_listed_fields_and_cleans_names():
    directory = FakeDirectory()
    now = FakeNow()()
    await directory.replace_all([
        Account("s1", "alice", True, ("product", "bad,name", "product"), ("agent-user",)),
        Account("s2", "service-account-bot", False, (), (), "service")], now)
    alice = await directory.lookup("s1")
    assert alice.groups == ("product",) and alice.roles == ("agent-user",) and alice.synced_at == now
    await directory.replace_all([Account("s2", "service-account-bot", True, (), (), "service")], now)
    assert await directory.lookup("s1") is None and (await directory.lookup("s2")).enabled
    assert await directory.newest_sync() == now
