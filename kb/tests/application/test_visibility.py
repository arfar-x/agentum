"""Group visibility through the use cases, against the in-memory store."""

from __future__ import annotations

from datetime import datetime, timezone

from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.content import prepare
from kb.domain.entry import Entry, EntryType, Location
from kb.domain.visibility import Caller
from tests.application.fakes import FakeClock, FakeEntryStore

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def entry(source: str, n: int = 1) -> Entry:
    return Entry(
        id=f"{source}:{n}",
        type=EntryType.DOC,
        title={"en": f"Refund policy {source}"},
        summary={"en": "Refund retries."},
        keywords=("refund",),
        location=Location(kind="gitlab", ref={"path": "a.md"}, url="https://x/a"),
        source_id=source,
        source_version="v1",
        last_seen_at=NOW,
    )


def catalog():
    store = FakeEntryStore()
    store.upsert(entry("open"))
    store.upsert(entry("eng-only"))
    store.apply_visibility({"open": (), "eng-only": ("eng",)}, at=NOW)
    store.put_content("eng-only:1", prepare("secret text", 10_000), source_version="v1")
    clock = FakeClock(NOW)
    return store, SearchCatalog(store, clock), GetEntry(store, clock)


def ids(result):
    return {hit.entry.id for hit in result.hits}


def test_search_shows_only_what_the_caller_may_see():
    # Covers: FR-VIS-06
    _, search, _ = catalog()
    assert ids(search.execute(["refund"])) == {"open:1"}
    assert ids(search.execute(["refund"], caller=Caller.in_groups(["ops"]))) == {"open:1"}
    assert ids(search.execute(["refund"], caller=Caller.in_groups(["eng"]))) == {"open:1", "eng-only:1"}


def test_a_restricted_entry_takes_no_result_slot():
    # Covers: FR-VIS-07
    _, search, _ = catalog()
    result = search.execute(["refund"], limit=1)
    assert [h.entry.id for h in result.hits] == ["open:1"]


def test_reading_a_hidden_entry_looks_like_reading_a_missing_one():
    # Covers: FR-VIS-08
    store, _, get = catalog()
    assert get.execute("eng-only:1") is None
    assert get.execute("nothing:1") is None
    assert get.execute("eng-only:1", caller=Caller.in_groups(["eng"])) is not None
    assert "eng-only:1" not in store.refresh_queue


def test_stored_text_follows_its_entry():
    # Covers: FR-VIS-09
    _, _, get = catalog()
    assert get.execute("eng-only:1", offset=0) is None
    view = get.execute("eng-only:1", caller=Caller.in_groups(["eng"]))
    assert view.content.text == "secret text"


def test_a_source_without_a_row_is_invisible_to_everyone_but_operators():
    # Covers: FR-VIS-04
    store, search, get = catalog()
    del store.visibility["open"]
    assert ids(search.execute(["refund"])) == set()
    assert get.execute("open:1", caller=Caller.in_groups(["eng"])) is None
    assert ids(search.execute(["refund"], caller=Caller.operator_tool())) == {"open:1", "eng-only:1"}
    assert get.execute("open:1", caller=Caller.operator_tool()) is not None


def test_a_removed_sources_row_survives_while_it_has_entries():
    # Covers: FR-VIS-05
    store, _, _ = catalog()
    store.apply_visibility({"open": ()}, at=NOW)
    assert store.source_groups() == {"open": (), "eng-only": ("eng",)}
    store.mark_missing(store.ids_for_source("eng-only"), at=NOW)
    store.apply_visibility({"open": ()}, at=NOW)
    assert store.source_groups() == {"open": ()}
