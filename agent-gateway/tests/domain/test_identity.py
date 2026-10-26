# Covers: FR-IDN-08, FR-IDN-05, FR-IDN-02, FR-AUT-02

from dataclasses import fields
from datetime import datetime, timedelta, timezone

from agent_gateway.domain.identity import (
    DirectoryEntry,
    KeyInfo,
    KeyScope,
    Principal,
    clean_groups,
    clean_roles,
    hash_key,
    is_active,
    scope_from_json,
    scope_to_json,
    valid_group,
    valid_name,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_a_valid_name_is_non_empty_short_and_free_of_commas_and_control_characters():
    assert valid_group("product") and valid_name("agent-writer") and valid_name("Team A/B")
    assert not valid_group("") and not valid_group("   ")
    assert not valid_group("a,b")
    assert not valid_group("a\nb") and not valid_group("a\x00b") and not valid_group("tab\there")
    assert valid_group("x" * 128) and not valid_group("x" * 129)
    assert not valid_group(None) and not valid_group(7)


def test_cleaning_drops_invalid_names_and_duplicates_and_keeps_order():
    raw = ["b", "a", "b", "", "x,y", "a", "c\n"]
    assert clean_groups(raw) == ("b", "a")
    assert clean_roles(iter(["r2", "r1", "r2"])) == ("r2", "r1")
    assert clean_groups(["Admin", "admin"]) == ("Admin", "admin")          # case-sensitive, exact


def test_a_key_is_active_until_it_is_revoked_or_expires():
    def key(**kw):
        base = dict(id="k", owner="o", kind="issued", label="", prefix="agk_", scope=KeyScope(),
                    created_at=NOW, expires_at=None, revoked_at=None, last_used_at=None)
        return KeyInfo(**{**base, **kw})

    assert is_active(key(), NOW)
    assert is_active(key(expires_at=NOW + timedelta(seconds=1)), NOW)
    assert not is_active(key(expires_at=NOW), NOW)
    assert not is_active(key(revoked_at=NOW - timedelta(days=1)), NOW)


def test_keys_are_recognised_by_a_one_way_hash_and_scopes_round_trip_through_json():
    assert hash_key("agk_x") == hash_key("agk_x") != hash_key("agk_y")
    assert "agk_x" not in hash_key("agk_x") and len(hash_key("agk_x")) == 64
    scope = KeyScope(("a", "b"), False)
    assert scope_from_json(scope_to_json(scope)) == scope
    assert scope_from_json(None) == KeyScope()


def test_a_principal_defaults_to_a_person_with_the_widest_scope_and_the_copy_holds_no_email():
    p = Principal("sub-1", "alice")
    assert (p.kind, p.key_id, p.scope, p.groups, p.roles) == ("user", None, KeyScope(), (), ())
    names = {f.name for f in fields(DirectoryEntry)}
    assert names == {"user_id", "username", "groups", "roles", "enabled", "synced_at", "kind"}
