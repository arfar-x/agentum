"""An installation that already has data takes migration 004 without losing it.

The other integration tests start from an empty database, which is the one case
an upgrade never is: a real deployment has months of run history written by the
version before the console existed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
UPGRADE_DB = "kb_upgrade_test"


@pytest.fixture()
def old_database(dsn, tmp_path):
    """A database as the version before the console left it: 001-003 applied."""
    from psycopg.conninfo import make_conninfo

    maintenance = make_conninfo(dsn, dbname="postgres")
    target = make_conninfo(dsn, dbname=UPGRADE_DB)
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {UPGRADE_DB}")
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    before = tmp_path / "before"
    before.mkdir()
    for name in ("001_initial.sql", "002_refresh_queue.sql", "003_entry_content.sql"):
        shutil.copy(MIGRATIONS / name, before / name)
    try:
        yield target, before
    finally:
        with psycopg.connect(maintenance, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")


# Covers: FR-SCH-08
def test_history_written_before_the_console_survives_the_upgrade(old_database):
    from kb.adapters.outbound.postgres_store import PostgresEntryStore
    from kb.adapters.outbound.postgres_runs import PostgresRunLog
    from kb.domain.runs import RunStatus, Trigger

    target, before = old_database
    store = PostgresEntryStore.connect(target)
    assert store.migrate(before) == ["001_initial.sql", "002_refresh_queue.sql", "003_entry_content.sql"]
    with store.connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sync_run (source_id, mechanism, started_at, finished_at, created, updated, summarized, error) VALUES"
            " ('jira', 'incremental', now() - interval '2 days', now() - interval '2 days', 5, 1, 6, NULL),"
            " ('jira', 'incremental', now() - interval '1 day', now() - interval '1 day', 0, 0, 0, 'timed out'),"
            " ('wiki', 'full_scrape', now() - interval '3 hours', now() - interval '3 hours', 9, 0, 9, NULL)"
        )
    store.set_checkpoint("jira", "2026-10-01 10:00")

    assert store.migrate(MIGRATIONS) == ["004_admin_console.sql"], "only the new migration is applied"
    assert store.migrate(MIGRATIONS) == [], "and it is a no-op the second time"

    runs = PostgresRunLog(store.connection).list_runs(limit=10)
    by_source = {(r.source_id, r.error is not None): r for r in runs}
    assert len(runs) == 3, "no run was lost"
    ok = by_source[("jira", False)]
    assert (ok.status, ok.triggered_by, ok.created, ok.summarized) == (RunStatus.SUCCESS, Trigger.SCHEDULED, 5, 6)
    assert by_source[("jira", True)].status is RunStatus.ERROR, "a failed run is still a failed run"
    assert by_source[("wiki", False)].status is RunStatus.SUCCESS
    assert all(r.dry_run is False and r.forced is False and r.rejected == () for r in runs)
    assert store.get_checkpoint("jira") == "2026-10-01 10:00", "checkpoints are untouched"

    # and the new tables are there, empty
    with store.connection.cursor() as cursor:
        for table in ("sync_request", "scheduler_heartbeat", "kb_source", "kb_source_revision", "form_record"):
            cursor.execute(f"SELECT count(*) AS n FROM {table}")
            assert cursor.fetchone()["n"] == 0, table
    store.close()
