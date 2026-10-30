# Covers: FR-KEY-01, FR-KEY-02, FR-KEY-03, FR-KEY-04, FR-KEY-05, FR-KEY-06, FR-KEY-07, FR-KEY-08
# Covers: FR-AUT-02, FR-AUT-03, FR-AUD-04

import pytest

from agent_gateway.application.key_service import KeyManager
from agent_gateway.domain.identity import KeyIssuingDisabled, KeyScope, hash_key
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
    assert await keys.resolve_all(MODEL_KEY) == []
    assert [i.id for i in await keys.resolve_all("sk-second-key-0000")] == [second.id]
    event = [e for e in audit.events if e.action == "key.register"][-1]
    assert event.detail["replaced"] == first.id


async def test_a_model_key_may_be_registered_by_several_people_and_the_second_is_never_told():
    keys, records, audit, _ = make()
    first = await keys.register_upstream("alice", "", MODEL_KEY)
    second = await keys.register_upstream("bob", "", MODEL_KEY)             # no refusal, nothing to learn from it
    assert first.id != second.id
    assert {i.owner for i in await keys.resolve_all(MODEL_KEY)} == {"alice", "bob"}
    assert await keys.upstream_key("alice") == await keys.upstream_key("bob") == MODEL_KEY
    await keys.revoke("alice", first.id)
    assert [i.owner for i in await keys.resolve_all(MODEL_KEY)] == ["bob"]   # one holder left: unambiguous again
    assert await keys.upstream_key("alice") is None and await keys.upstream_key("bob") == MODEL_KEY


async def test_one_person_still_has_at_most_one_active_model_key_whatever_others_hold():
    keys, records, _, _ = make()
    await keys.register_upstream("alice", "", MODEL_KEY)
    await keys.register_upstream("bob", "", MODEL_KEY)
    await keys.register_upstream("bob", "", "sk-bob-new-key-2222")
    assert [i.owner for i in await keys.resolve_all(MODEL_KEY)] == ["alice"]


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
    owner = lambda found: [i.owner for i in found]                           # noqa: E731
    assert owner(await keys.resolve_all(secret)) == ["alice"]
    assert owner(await keys.resolve_all(MODEL_KEY)) == ["bob"]
    assert await keys.resolve_all("agk_unknown") == []
    off = KeyManager(records, FakeSecretBox(), issue_keys=False, now=now)
    assert await off.resolve_all(secret) == [] and owner(await off.resolve_all(MODEL_KEY)) == ["bob"]
    now.advance(hours=2)
    assert await keys.resolve_all(secret) == []                              # expired
    secret2, other = await keys.issue("alice", "", KeyScope(), None)
    assert await keys.resolve_all(secret2) != []
    assert await keys.revoke("alice", other.id)
    assert await keys.resolve_all(secret2) == []                             # revocation: next request


async def test_last_used_is_updated_at_most_once_a_minute():
    keys, records, _, now = make()
    secret, info = await keys.issue("alice", "", KeyScope(), None)
    assert records.rows[info.id]["info"].last_used_at is None
    await keys.resolve_all(secret)
    first = records.rows[info.id]["info"].last_used_at
    assert first == now()
    now.advance(seconds=30)
    await keys.resolve_all(secret)
    assert records.rows[info.id]["info"].last_used_at == first
    now.advance(seconds=31)
    await keys.resolve_all(secret)
    assert records.rows[info.id]["info"].last_used_at == now()


async def test_a_person_revokes_only_their_own_keys_an_operator_any_and_revocation_is_final():
    keys, records, audit, _ = make()
    secret, info = await keys.issue("alice", "", KeyScope(), None)
    assert not await keys.revoke("bob", info.id)
    assert await keys.resolve_all(secret) != []
    assert await keys.revoke(None, info.id, actor="cli")
    assert await keys.resolve_all(secret) == []
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
    assert len(await keys.resolve_all(MODEL_KEY)) == 1
