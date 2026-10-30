"""Who is asking: the trust-header rule of the MCP server (FR-VIS-10/11)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from kb.adapters.inbound.mcp_server import build_app
from kb.application.use_cases.get_entry import GetEntry
from kb.application.use_cases.search_catalog import SearchCatalog
from kb.domain.entry import Entry, EntryType, Location
from tests.application.fakes import FakeClock, FakeEntryStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
SECRET = "s3cret-shared-value"

OPEN = Entry(
    id="open:1",
    type=EntryType.DOC,
    title={"en": "Payment reconciliation"},
    summary={"en": "How refund retries are handled."},
    keywords=("refund",),
    location=Location(kind="confluence", ref={"page_id": "1"}, url="https://wiki/1"),
    source_id="open",
    last_seen_at=NOW - timedelta(hours=1),
)
CLOSED = OPEN.model_copy(update={"id": "closed:1", "source_id": "closed"})


class StubContainer:
    def __init__(self, store: FakeEntryStore) -> None:
        clock = FakeClock(NOW)
        self.store = store
        self.search = SearchCatalog(store, clock)
        self.get_entry = GetEntry(store, clock)


def app_for(headers, *, trust_secret=SECRET):
    store = FakeEntryStore()
    store.upsert(OPEN)
    store.upsert(CLOSED)
    store.apply_visibility({"open": (), "closed": ("eng",)}, at=NOW)
    return build_app(StubContainer(store), trust_secret=trust_secret, headers=lambda: headers)


def call(app, name, **arguments):
    tool = asyncio.run(app.get_tool(name))
    return asyncio.run(tool.run(arguments)).structured_content


def seen(app):
    out = call(app, "kb_search", queries=["refund"], catalog_queries=["refund"])
    return {hit["id"] for hit in out["hits"]}


def test_groups_are_believed_only_with_the_matching_trust_secret():
    # Covers: FR-VIS-10
    trusted = {"x-kb-trust": SECRET, "x-kb-groups": "eng"}
    assert seen(app_for(trusted)) == {"open:1", "closed:1"}
    assert call(app_for(trusted), "kb_get", id="closed:1")["entry"]["id"] == "closed:1"

    for headers in (
        {"x-kb-groups": "eng"},
        {"x-kb-trust": "wrong", "x-kb-groups": "eng"},
        {"x-kb-trust": SECRET},
        {"x-kb-trust": SECRET, "x-kb-groups": ""},
        {"x-kb-trust": "é", "x-kb-groups": "eng"},
        {},
    ):
        assert seen(app_for(headers)) == {"open:1"}, headers
    forged = call(app_for({"x-kb-groups": "eng"}), "kb_get", id="closed:1")
    assert forged["error"]["type"] == "not_found"


def test_without_a_configured_secret_the_group_header_is_ignored():
    # Covers: FR-VIS-11
    headers = {"x-kb-trust": "anything", "x-kb-groups": "eng"}
    assert seen(app_for(headers, trust_secret=None)) == {"open:1"}
    assert seen(app_for({"x-kb-trust": "", "x-kb-groups": "eng"}, trust_secret="")) == {"open:1"}
