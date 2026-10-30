# Covers: FR-ATH-01, FR-ATH-13, FR-ATH-14, FR-KEY-03
"""A person's own model key as a bearer: unique owner only, scoped by GATEWAY_MODEL_KEY_LOGIN."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_gateway.application.authenticate import Authenticator
from agent_gateway.application.directory_sync import DirectorySync
from agent_gateway.application.key_service import KeyManager
from agent_gateway.domain.identity import AuthError, DirectoryEntry, KeyScope
from tests.application.fakes import (
    FakeAudit,
    FakeDirectory,
    FakeDirectorySource,
    FakeKeyRecords,
    FakeNow,
    FakeSecretBox,
    FakeTokenVerifier,
)

SHARED = "sk-colleagues-share-this-0001"
ALICE_OWN = "sk-alice-unique-0002"


class Rig:
    def __init__(self, mode="read-only", issue_keys=True):
        self.now = FakeNow()
        self.audit = FakeAudit(self.now)
        self.records = FakeKeyRecords(self.audit, self.now)
        self.keys = KeyManager(self.records, FakeSecretBox(), issue_keys=issue_keys, now=self.now)
        directory = FakeDirectory()
        for sub in ("alice", "bob", "carol"):
            directory.rows[sub] = DirectoryEntry(sub, sub, ("product",), ("agent-user",), True, self.now())
        sync = DirectorySync(FakeDirectorySource(), directory, self.audit, now=self.now)
        self.auth = Authenticator(self.keys, FakeTokenVerifier({}), directory, sync, self.audit,
                                  issue_keys=issue_keys, max_stale=timedelta(minutes=15), now=self.now,
                                  model_key_login=mode)

    async def denied(self, bearer):
        with pytest.raises(AuthError) as err:
            await self.auth.authenticate(bearer, path="/v1/models")
        return err.value


async def test_a_unique_model_key_logs_in_read_only_by_default():
    r = Rig()
    await r.keys.register_upstream("alice", "", ALICE_OWN)
    p = await r.auth.authenticate(ALICE_OWN)
    assert p.user_id == "alice" and p.scope == KeyScope(write=False)


async def test_full_mode_gives_the_default_scope_and_off_mode_never_authenticates():
    full, off = Rig("full"), Rig("off")
    for r in (full, off):
        await r.keys.register_upstream("alice", "", ALICE_OWN)
    assert (await full.auth.authenticate(ALICE_OWN)).scope == KeyScope()
    error = await off.denied(ALICE_OWN)
    assert error.status == 401 and error.code == "unknown_key"


async def test_a_string_two_people_registered_identifies_nobody_and_the_refusal_is_audited():
    r = Rig()
    await r.keys.register_upstream("alice", "", SHARED)
    await r.keys.register_upstream("bob", "", SHARED)
    error = await r.denied(SHARED)
    assert (error.status, error.code) == (401, "model_key_shared")
    [event] = [e for e in r.audit.events if e.action == "auth.denied"]
    assert event.target == "model_key_shared" and "alice" not in repr(event) and "bob" not in repr(event)
    assert SHARED not in repr(event)


async def test_an_unknown_string_gets_the_ordinary_refusal_not_the_shared_one():
    r = Rig()
    await r.keys.register_upstream("alice", "", SHARED)
    await r.keys.register_upstream("bob", "", SHARED)
    assert (await r.denied("sk-nobody-has-this")).code == "unknown_key"


async def test_shared_keys_are_refused_in_every_mode_but_off_where_they_are_just_unknown():
    for mode, code in (("read-only", "model_key_shared"), ("full", "model_key_shared"), ("off", "unknown_key")):
        r = Rig(mode)
        await r.keys.register_upstream("alice", "", SHARED)
        await r.keys.register_upstream("bob", "", SHARED)
        assert (await r.denied(SHARED)).code == code


async def test_when_one_holder_revokes_the_other_becomes_a_valid_read_only_login():
    """Documented behaviour: sharing a key disables it as a login, and the end of the sharing re-enables it."""
    r = Rig()
    a = await r.keys.register_upstream("alice", "", SHARED)
    await r.keys.register_upstream("bob", "", SHARED)
    assert (await r.denied(SHARED)).code == "model_key_shared"
    assert await r.keys.revoke("alice", a.id)
    p = await r.auth.authenticate(SHARED)
    assert p.user_id == "bob" and p.scope == KeyScope(write=False)


async def test_an_issued_key_is_unaffected_by_the_mode_and_keeps_its_own_scope():
    for mode in ("off", "read-only", "full"):
        r = Rig(mode)
        secret, _ = await r.keys.issue("alice", "", KeyScope(("router",), True), None)
        p = await r.auth.authenticate(secret)
        assert p.user_id == "alice" and p.scope == KeyScope(("router",), True)


async def test_a_revoked_model_key_does_not_count_toward_sharing():
    r = Rig()
    await r.keys.register_upstream("alice", "", SHARED)
    b = await r.keys.register_upstream("bob", "", SHARED)
    await r.keys.revoke("bob", b.id)
    assert (await r.auth.authenticate(SHARED)).user_id == "alice"
