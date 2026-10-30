# Covers: FR-KEY-01, FR-KEY-02, FR-KEY-03, FR-KEY-04, FR-KEY-05, FR-KEY-06, FR-KEY-07, FR-KEY-08
# Covers: FR-AUT-02, FR-AUT-03, FR-AUD-04

import pytest

from agent_gateway.application.key_service import KeyManager
from agent_gateway.domain.identity import DuplicateKey, KeyIssuingDisabled, KeyScope, hash_key
from tests.application.fakes import FakeAudit, FakeKeyRecords, FakeNow, FakeSecretBox

MODEL_KEY = "sk-live-0123456789abcdef-WXYZ"


def make(issue_keys=True):
    now = FakeNow()
    audit = FakeAudit(now)
    records = FakeKeyRecords(audit, now)
    return KeyManager(records, FakeSecretBox(), issue_keys=issue_keys, now=now), records, audit, now


async def test_a_model_key_is_stored_as_hash_and_encrypted_copy_with_a_fragment_and_never_listed():
    keys, records, _, _ = make()
    info = await keys.register_upstream("alice", "mine", MODEL_KEY)
    row = records.rows[info.id]
    assert row["key_hash"] == hash_key(MODEL_KEY)
    assert MODEL_KEY.encode() not in row["secret_enc"]
    assert info.kind == "upstream" and info.prefix == "WXYZ" and MODEL_KEY[:4] not in info.prefix
    assert await keys.upstream_key("alice") == MODEL_KEY
    assert MODEL_KEY not in repr(await keys.list_for("alice"))


async def test_registering_another_key_revokes_the_first_and_destroys_its_copy():
    keys, records, audit, _ = make()
    first = await keys.register_upstream("alice", "old", MODEL_KEY)
    second = await keys.register_upstream("alice", "new", "sk-second-key-0000")
    assert records.rows[first.id]["info"].revoked_at is not None and records.rows[first.id]["secret_enc"] is None
    assert await keys.upstream_key("alice") == "sk-second-key-0000"
    assert await keys.resolve(MODEL_KEY) is None
    assert (await keys.resolve("sk-second-key-0000")).id == second.id
    event = [e for e in audit.events if e.action == "key.register"][-1]
    assert event.detail["replaced"] == first.id


async def test_a_key_active_under_another_owner_is_refused_without_saying_whose_and_changes_nothing():
    keys, records, audit, _ = make()
    await keys.register_upstream("alice", "", MODEL_KEY)
    mine = await keys.register_upstream("bob", "", "sk-bobs-own-key-1111")
    before = len(audit.events)
    with pytest.raises(DuplicateKey) as err:
        await keys.register_upstream("bob", "", MODEL_KEY)
    assert "alice" not in str(err.value)
    assert (await keys.upstream_key("bob")) == "sk-bobs-own-key-1111"       # bob's earlier key survives
    assert records.rows[mine.id]["info"].revoked_at is None
    assert len(audit.events) == before                                      # a refused change leaves no entry
    await keys.revoke("alice", (await keys.list_for("alice"))[0].id)
    await keys.register_upstream("bob", "", MODEL_KEY)                      # a revoked key's hash is free again


async def test_issuing_is_refused_unless_the_flag_is_on():
    keys, records, _, _ = make(issue_keys=False)
    with pytest.raises(KeyIssuingDisabled):
        await keys.issue("alice", "", KeyScope(), None)
    assert records.rows == {}


async def test_an_issued_key_is_long_random_shown_once_hashed_and_carries_expiry_and_scope():
    keys, records, _, now = make()
    expires = now() + __import__("datetime").timedelta(days=30)
    secret, info = await keys.issue("alice", "laptop", KeyScope(("tasks",), False), expires)
    other, _ = await keys.issue("alice", "", KeyScope(), None)
    assert secret.startswith("agk_") and len(secret) >= 4 + 43 and secret != other
    row = records.rows[info.id]
    assert row["key_hash"] == hash_key(secret) and row["secret_enc"] is None
    assert info.prefix == secret[:8] and info.expires_at == expires
    assert info.scope == KeyScope(("tasks",), False) and info.owner == "alice"
    assert secret not in repr(await keys.list_for("alice"))


async def test_listing_shows_metadata_for_every_kind_and_status_and_no_secret():
    keys, _, _, _ = make()
    _, issued = await keys.issue("alice", "a", KeyScope(), None)
    registered = await keys.register_upstream("alice", "b", MODEL_KEY)
    await keys.revoke("alice", issued.id)
    listed = {k.id: k for k in await keys.list_for("alice")}
    assert listed[issued.id].revoked_at is not None and listed[issued.id].kind == "issued"
    assert listed[registered.id].kind == "upstream" and listed[registered.id].label == "b"
    assert await keys.list_for("bob") == []
    assert not any(hasattr(k, "secret") or hasattr(k, "key_hash") for k in listed.values())


async def test_resolve_accepts_any_kind_but_not_revoked_expired_unknown_or_issued_when_the_flag_is_off():
    keys, records, _, now = make()
    secret, issued = await keys.issue("alice", "", KeyScope(), now() + __import__("datetime").timedelta(hours=1))
    await keys.register_upstream("bob", "", MODEL_KEY)
    assert (await keys.resolve(secret)).owner == "alice"
    assert (await keys.resolve(MODEL_KEY)).owner == "bob"
    assert await keys.resolve("agk_unknown") is None
    off = KeyManager(records, FakeSecretBox(), issue_keys=False, now=now)
    assert await off.resolve(secret) is None and (await off.resolve(MODEL_KEY)).owner == "bob"
    now.advance(hours=2)
    assert await keys.resolve(secret) is None                                # expired
    secret2, other = await keys.issue("alice", "", KeyScope(), None)
    assert await keys.resolve(secret2) is not None
    assert await keys.revoke("alice", other.id)
    assert await keys.resolve(secret2) is None                               # revocation: next request


async def test_last_used_is_updated_at_most_once_a_minute():
    keys, records, _, now = make()
    secret, info = await keys.issue("alice", "", KeyScope(), None)
    assert records.rows[info.id]["info"].last_used_at is None
    await keys.resolve(secret)
    first = records.rows[info.id]["info"].last_used_at
    assert first == now()
    now.advance(seconds=30)
    await keys.resolve(secret)
    assert records.rows[info.id]["info"].last_used_at == first
    now.advance(seconds=31)
    await keys.resolve(secret)
    assert records.rows[info.id]["info"].last_used_at == now()


async def test_a_person_revokes_only_their_own_keys_an_operator_any_and_revocation_is_final():
    keys, records, audit, _ = make()
    secret, info = await keys.issue("alice", "", KeyScope(), None)
    assert not await keys.revoke("bob", info.id)
    assert await keys.resolve(secret) is not None
    assert await keys.revoke(None, info.id, actor="cli")
    assert await keys.resolve(secret) is None
    assert not await keys.revoke(None, info.id) and not await keys.revoke("alice", info.id)
    assert not await keys.revoke(None, "not-a-uuid")
    event = [e for e in audit.events if e.action == "key.revoke"][0]
    assert (event.actor, event.target, event.detail) == ("cli", info.id, {"by": "operator"})
    assert records.rows[info.id]["info"].revoked_at is not None


async def test_an_empty_model_key_is_refused_and_surrounding_whitespace_is_ignored():
    keys, _, _, _ = make()
    with pytest.raises(ValueError):
        await keys.register_upstream("alice", "", " \n")
    await keys.register_upstream("alice", "", "  " + MODEL_KEY + "\n")
    assert await keys.upstream_key("alice") == MODEL_KEY
    assert (await keys.resolve(MODEL_KEY)) is not None
