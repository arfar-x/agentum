# Covers: FR-IDN-03, FR-IDN-04, FR-AUD-02, FR-AUD-06
from datetime import timedelta

import pytest

from agent_gateway.application.audit import prune_audit
from agent_gateway.application.directory_sync import DirectorySync
from agent_gateway.application.identity_ports import Account
from tests.application.fakes import FakeAudit, FakeDirectory, FakeDirectorySource, FakeNow


def rig():
    now = FakeNow()
    audit, directory, source = FakeAudit(now), FakeDirectory(), FakeDirectorySource()
    return now, audit, directory, source, DirectorySync(source, directory, audit, now=now)


async def test_a_refresh_replaces_the_whole_copy_with_one_sync_time():
    now, audit, directory, source, sync = rig()
    source.accounts = [Account("a", "alice", True, ("g",), ("r",)), Account("b", "bob", False, (), ())]
    await sync.refresh()
    assert set(directory.rows) == {"a", "b"} and {e.synced_at for e in directory.rows.values()} == {now()}
    now.advance(minutes=5)
    source.accounts = [Account("b", "bob", True, ("x",), ())]
    await sync.refresh()
    assert set(directory.rows) == {"b"} and directory.rows["b"].enabled and directory.rows["b"].synced_at == now()


async def test_a_failure_keeps_the_copy_and_is_audited_once_per_streak_then_recovery_once():
    now, audit, directory, source, sync = rig()
    source.accounts = [Account("a", "alice", True, (), ())]
    await sync.refresh()
    source.error = RuntimeError("keycloak unreachable")
    for _ in range(3):
        now.advance(minutes=5)
        with pytest.raises(RuntimeError):
            await sync.refresh()
    assert set(directory.rows) == {"a"}
    failed = [e for e in audit.events if e.action == "directory.sync_failed"]
    assert len(failed) == 1 and failed[0].actor == "system" and "unreachable" in failed[0].detail["error"]
    source.error = None
    await sync.refresh()
    await sync.refresh()
    assert len([e for e in audit.events if e.action == "directory.sync_recovered"]) == 1


async def test_an_on_demand_refresh_waits_thirty_seconds_and_never_raises():
    now, audit, directory, source, sync = rig()
    source.error = RuntimeError("down")
    await sync.refresh_if_due(timedelta(seconds=30))
    await sync.refresh_if_due(timedelta(seconds=30))
    assert source.calls == 1
    now.advance(seconds=31)
    await sync.refresh_if_due(timedelta(seconds=30))
    assert source.calls == 2


async def test_old_audit_entries_are_pruned():
    now, audit, *_ = rig()
    await audit.record("x", "auth.denied", "a")
    now.advance(days=400)
    await audit.record("x", "auth.denied", "b")
    assert await prune_audit(audit, 365, now()) == 1
    assert [e.target for e in audit.events] == ["b"]


async def test_an_empty_answer_is_a_failure_and_deletes_nobody():
    now, audit, directory, source, sync = rig()
    source.accounts = [Account("a", "alice", True, (), ())]
    await sync.refresh()
    source.accounts = []
    with pytest.raises(ValueError):
        await sync.refresh()
    assert set(directory.rows) == {"a"}
