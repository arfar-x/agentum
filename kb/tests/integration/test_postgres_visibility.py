"""Group visibility in the SQL itself (spec §6.12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb.domain.entry import Entry, EntryType, Location
from kb.domain.text import normalize
from kb.domain.visibility import Caller

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def entry(source: str, n: int, word: str) -> Entry:
    return Entry(
        id=f"{source}:{n}",
        type=EntryType.DOC,
        title={"en": f"{word} notes {n}"},
        keywords=(word,),
        location=Location(kind="gitlab", ref={"path": "a.md"}, url=f"https://x/{source}/{n}"),
        source_id=source,
        last_seen_at=NOW,
    )


@pytest.fixture()
def two_sources(store):
    for n in range(1, 4):
        store.upsert(entry("open", n, "billing"))
        store.upsert(entry("closed", n, "billing"))
    store.apply_visibility({"open": (), "closed": ("eng", "ops")}, at=NOW)
    return store


def found(store, caller, **kwargs):
    return store.search(normalize("billing"), caller=caller, **kwargs)


def test_search_filters_inside_the_query(two_sources):
    # Covers: FR-VIS-06, FR-VIS-07
    anyone = Caller.anonymous()
    assert {i.split(":")[0] for i in found(two_sources, anyone)} == {"open"}
    assert {i.split(":")[0] for i in found(two_sources, Caller.in_groups(["nope"]))} == {"open"}
    assert len(found(two_sources, Caller.in_groups(["ops"]))) == 6
    assert len(found(two_sources, Caller.operator_tool())) == 6
    # The limit counts only what the caller can see: three hits exist for them.
    assert len(found(two_sources, anyone, limit=5)) == 3


def test_reading_an_entry_obeys_the_same_rule(two_sources):
    # Covers: FR-VIS-08
    assert two_sources.get_visible("closed:1", Caller.anonymous()) is None
    assert two_sources.get_visible("missing:1", Caller.anonymous()) is None
    assert two_sources.get_visible("closed:1", Caller.in_groups(["eng"])).id == "closed:1"
    assert two_sources.get_visible("open:1", Caller.anonymous()).id == "open:1"


def test_a_source_without_a_row_is_invisible(store):
    # Covers: FR-VIS-04
    store.upsert(entry("orphan", 1, "billing"))
    assert found(store, Caller.in_groups(["eng"])) == []
    assert store.get_visible("orphan:1", Caller.in_groups(["eng"])) is None
    assert found(store, Caller.operator_tool()) == ["orphan:1"]


def test_a_removed_sources_row_waits_for_its_entries_to_go(two_sources):
    # Covers: FR-VIS-05
    two_sources.apply_visibility({"open": ()}, at=NOW + timedelta(minutes=1))
    assert two_sources.source_groups() == {"open": (), "closed": ("eng", "ops")}
    two_sources.mark_missing(two_sources.ids_for_source("closed"), at=NOW)
    two_sources.apply_visibility({"open": ()}, at=NOW + timedelta(minutes=2))
    assert two_sources.source_groups() == {"open": ()}


def test_changed_groups_take_effect_at_once(two_sources):
    # Covers: FR-VIS-03
    two_sources.apply_visibility({"open": (), "closed": ("legal",)}, at=NOW)
    assert len(found(two_sources, Caller.in_groups(["eng"]))) == 3
    assert len(found(two_sources, Caller.in_groups(["legal"]))) == 6
