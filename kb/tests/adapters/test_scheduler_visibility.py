"""The scheduler records each source's groups before its entries (FR-VIS-03)."""

from __future__ import annotations

from datetime import timedelta

from kb.domain.runs import Trigger
from tests.adapters.test_scheduler import CONFIG, NOON, wired  # noqa: F401 - fixture


def test_the_groups_are_recorded_before_any_entry_is_written(wired):  # noqa: F811
    # Covers: FR-VIS-03
    container, store, scheduler, path = wired
    path.write_text(CONFIG.replace("spaces: [ENG]", "spaces: [ENG]\n    groups: [eng]"), encoding="utf-8")

    order = []
    original_upsert = store.upsert
    store.upsert = lambda entry: (order.append(("entry", dict(store.visibility))), original_upsert(entry))[1]

    scheduler.tick(NOON)

    assert order, "the run wrote an entry"
    assert order[0][1].get("confluence-eng") == ("eng",), "the row existed when the first entry landed"
    assert store.source_groups()["confluence-eng"] == ("eng",)
    assert store.source_groups()["form"] == (), "manual records are unrestricted"


def test_a_change_of_groups_is_applied_on_the_next_tick_without_a_sync(wired):  # noqa: F811
    # Covers: FR-VIS-03
    _, store, scheduler, path = wired
    scheduler.tick(NOON)
    assert store.source_groups()["confluence-eng"] == ()

    path.write_text(CONFIG.replace("spaces: [ENG]", "spaces: [ENG]\n    groups: [eng]"), encoding="utf-8")
    scheduler.tick(NOON + timedelta(minutes=1))  # nothing is due, the table still follows the file

    assert store.source_groups()["confluence-eng"] == ("eng",)


def test_a_source_dropped_from_the_file_keeps_its_row_until_its_entries_are_retired(wired):  # noqa: F811
    # Covers: FR-VIS-05
    _, store, scheduler, path = wired
    path.write_text(CONFIG.replace("spaces: [ENG]", "spaces: [ENG]\n    groups: [eng]"), encoding="utf-8")
    scheduler.tick(NOON)
    path.write_text("approved: true\nsources: []\n", encoding="utf-8")

    scheduler.tick(NOON + timedelta(minutes=1))
    assert store.source_groups()["confluence-eng"] == ("eng",), "still protected while entries are live"

    scheduler._perform(
        mechanism="retire", source_id="confluence-eng", config=None, triggered_by=Trigger.MANUAL
    )
    assert "confluence-eng" not in store.source_groups()
