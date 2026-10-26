# Covers: FR-IDN-02, FR-IDN-06, FR-ATH-01, FR-ATH-04, FR-ATH-05, FR-ATH-06, FR-ATH-07, FR-ATH-08, FR-ATH-11, FR-AUD-02
"""The Authenticator: bearer -> Principal, over fakes and a fake clock."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_gateway.application.authenticate import Authenticator
from agent_gateway.application.directory_sync import DirectorySync
from agent_gateway.application.identity_ports import Account, Identity, NewKey
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

STALE = timedelta(minutes=15)


class Rig:
    def __init__(self, *, issue_keys=True):
        self.now = FakeNow()
        self.audit = FakeAudit(self.now)
        self.records = FakeKeyRecords(self.audit, self.now)
        self.keys = KeyManager(self.records, FakeSecretBox(), issue_keys=issue_keys, now=self.now)
        self.directory = FakeDirectory()
        self.source = FakeDirectorySource()
        self.sync = DirectorySync(self.source, self.directory, self.audit, now=self.now)
        self.identity = Identity("sub-t", "tina", ("g",), ("r",), client="telegram-bot")
        self.tokens = FakeTokenVerifier({"jwt.a.b": self.identity})
        self.auth = Authenticator(self.keys, self.tokens, self.directory, self.sync, self.audit,
                                  issue_keys=issue_keys, max_stale=STALE, now=self.now)

    def person(self, sub="sub-a", *, enabled=True, kind="user", groups=("product",), roles=("agent-user",), age=0):
        self.directory.rows[sub] = DirectoryEntry(sub, "name-" + sub, groups, roles, enabled,
                                                  self.now() - timedelta(seconds=age), kind)

    async def denied(self, bearer, code, status=401):
        with pytest.raises(AuthError) as err:
            await self.auth.authenticate(bearer, path="/v1/models")
        assert (err.value.code, err.value.status) == (code, status)
        return err.value


async def test_a_key_caller_gets_the_owners_directory_entry():
    r = Rig()
    r.person(groups=("product", "leads"), roles=("agent-writer",))
    secret, info = await r.keys.issue("sub-a", "x", KeyScope(("router",), False), None)
    p = await r.auth.authenticate(secret)
    assert (p.user_id, p.username, p.groups, p.roles, p.kind) == ("sub-a", "name-sub-a", ("product", "leads"),
                                                                 ("agent-writer",), "user")
    assert p.key_id == info.id and p.scope == KeyScope(("router",), False)


async def test_keys_are_looked_up_before_tokens():
    r = Rig()
    r.person()
    secret, _ = await r.keys.issue("sub-a", "x", KeyScope(), None)
    r.tokens.known[secret] = r.identity                  # would verify as a token too
    assert (await r.auth.authenticate(secret)).user_id == "sub-a"


async def test_a_token_caller_takes_identity_groups_and_roles_from_the_token():
    r = Rig()
    r.person("sub-t", groups=("old",), roles=())          # the copy does not decide for a token
    p = await r.auth.authenticate("jwt.a.b")
    assert (p.user_id, p.username, p.groups, p.roles, p.key_id) == ("sub-t", "tina", ("g",), ("r",), None)
    assert p.scope == KeyScope()


async def test_a_service_account_is_a_service_principal():
    r = Rig()
    r.person("sub-s", kind="service", groups=("automation",))
    secret, _ = await r.keys.issue("sub-s", "x", KeyScope(), None)
    assert (await r.auth.authenticate(secret)).kind == "service"
    r.tokens.known["svc.t.k"] = Identity("sub-s", "service-account-x", (), (), "service")
    assert (await r.auth.authenticate("svc.t.k")).kind == "service"


async def test_no_valid_bearer_is_refused_and_audited_with_a_reason_code():
    r = Rig()
    await r.denied(None, "no_bearer")
    await r.denied("   ", "no_bearer")
    await r.denied("x" * 9000, "malformed")
    await r.denied("what", "unknown_key")
    await r.denied("a.b.c", "bad_signature")
    events = {(e.actor, e.action, e.target) for e in r.audit.events}
    assert ("anonymous", "auth.denied", "no_bearer") in events and ("anonymous", "auth.denied", "unknown_key") in events
    assert all("a.b.c" not in str(e.detail) and "what" not in str(e.detail) for e in r.audit.events)
    assert r.audit.events[0].detail == {"path": "/v1/models", "reason": "no_bearer"}


async def test_a_revoked_or_expired_key_is_refused_on_the_next_request():
    r = Rig()
    r.person()
    secret, info = await r.keys.issue("sub-a", "x", KeyScope(), r.now() + timedelta(hours=1))
    await r.auth.authenticate(secret)
    r.now.advance(hours=2)
    r.person()
    await r.denied(secret, "unknown_key")
    secret2, info2 = await r.keys.issue("sub-a", "y", KeyScope(), None)
    await r.auth.authenticate(secret2)
    await r.keys.revoke(None, info2.id)
    await r.denied(secret2, "unknown_key")


async def test_an_issued_key_is_refused_while_issuing_is_off():
    r = Rig()
    r.person()
    secret, _ = await r.keys.issue("sub-a", "x", KeyScope(), None)
    off = Rig(issue_keys=False)
    off.records, off.keys._records = r.records, r.records
    off.person()
    await off.denied(secret, "issuing_disabled")


async def test_an_unknown_owner_triggers_one_refresh_then_is_refused():
    r = Rig()
    r.person("sub-other")
    r.source.accounts = [Account("sub-other", "other", True, (), ())]
    secret, _ = await r.keys.issue("sub-new", "x", KeyScope(), None)
    await r.denied(secret, "unknown_person")
    assert r.source.calls == 1
    await r.denied(secret, "unknown_person")              # inside the 30 second floor
    assert r.source.calls == 1
    r.now.advance(seconds=31)
    r.source.accounts.append(Account("sub-new", "newbie", True, ("product",), ()))
    assert (await r.auth.authenticate(secret)).username == "newbie"
    assert r.source.calls == 2


async def test_a_disabled_person_is_refused_for_both_kinds_of_bearer():
    r = Rig()
    r.person("sub-a", enabled=False)
    r.person("sub-t", enabled=False)
    secret, _ = await r.keys.issue("sub-a", "x", KeyScope(), None)
    await r.denied(secret, "person_disabled")
    await r.denied("jwt.a.b", "person_disabled")
    assert any(e.actor == "sub-a" and e.target == "person_disabled" for e in r.audit.events)


async def test_a_token_is_not_refused_for_a_missing_or_stale_entry():
    r = Rig()
    assert (await r.auth.authenticate("jwt.a.b")).user_id == "sub-t"          # no entry at all
    r.person("sub-t", enabled=False, age=3600)                                # stale row says disabled
    assert (await r.auth.authenticate("jwt.a.b")).user_id == "sub-t"


async def test_a_stale_copy_gives_503_with_a_retry_hint_to_key_callers_only():
    r = Rig()
    r.person(age=1000)
    r.source.error = RuntimeError("provider down")
    secret, _ = await r.keys.issue("sub-a", "x", KeyScope(), None)
    err = await r.denied(secret, "directory_stale", 503)
    assert err.retry_after == 30
    assert (await r.auth.authenticate("jwt.a.b")).user_id == "sub-t"
    r.person(age=899)
    assert (await r.auth.authenticate(secret)).user_id == "sub-a"             # inside the limit


async def test_a_copy_that_was_never_filled_is_unavailable_not_unknown():
    r = Rig()
    secret, _ = await r.keys.issue("sub-a", "x", KeyScope(), None)
    await r.denied(secret, "directory_stale", 503)


async def test_the_first_token_of_a_person_and_client_is_audited_once_a_day():
    r = Rig()
    await r.auth.authenticate("jwt.a.b")
    await r.auth.authenticate("jwt.a.b")
    firsts = [e for e in r.audit.events if e.action == "auth.first_use"]
    assert len(firsts) == 1 and (firsts[0].actor, firsts[0].target) == ("sub-t", "telegram-bot")
    r.now.advance(hours=25)
    await r.auth.authenticate("jwt.a.b")
    assert len([e for e in r.audit.events if e.action == "auth.first_use"]) == 2


async def test_a_failing_audit_never_changes_the_outcome():
    r = Rig()

    async def broken(*a, **k):
        raise RuntimeError("db down")

    r.audit.record = broken
    await r.denied("nope", "unknown_key")
    r.person("sub-t")
    assert (await r.auth.authenticate("jwt.a.b")).user_id == "sub-t"


async def test_a_registered_model_key_authenticates_as_its_owner():
    r = Rig()
    r.person()
    info = await r.keys.register_upstream("sub-a", "mine", "sk-model-key-1234")
    p = await r.auth.authenticate("sk-model-key-1234")
    assert p.user_id == "sub-a" and p.key_id == info.id


def test_newkey_import_is_available():                    # keeps NewKey exercised by the fakes' contract
    assert NewKey
