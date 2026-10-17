"""A connection that outlives a database restart, proved without a database."""

from __future__ import annotations

import pytest

from kb.adapters.outbound.postgres_connection import PING_AFTER_SECONDS, ResilientConnection

psycopg = pytest.importorskip("psycopg")


class FakeConnection:
    """What `ResilientConnection` needs from a connection, and no more."""

    def __init__(self, name: str, *, dies_on_ping: bool = False) -> None:
        self.name = name
        self.closed = False
        self.broken = False
        self.row_factory = None
        self.queries: list[str] = []
        self.dies_on_ping = dies_on_ping

    def execute(self, query, *args, **kwargs):
        self.queries.append(query)
        if self.dies_on_ping:
            self.broken = True
            raise psycopg.OperationalError("the connection is lost")

    def cursor(self, *args, **kwargs):
        return ("cursor", self.name)

    def transaction(self, *args, **kwargs):
        return ("transaction", self.name)

    def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def wire(*connections: FakeConnection):
    pending = list(connections)
    opened: list[FakeConnection] = []

    def connect():
        conn = pending.pop(0)
        opened.append(conn)
        return conn

    clock = Clock()
    return ResilientConnection(connect, clock=clock), opened, clock


def test_a_connection_psycopg_knows_is_lost_is_replaced_before_the_next_use():
    # Covers: NFR-DEP-09
    resilient, opened, _ = wire(FakeConnection("a"), FakeConnection("b"))
    assert resilient.cursor() == ("cursor", "a")
    opened[0].broken = True
    assert resilient.cursor() == ("cursor", "b")
    assert resilient.transaction() == ("transaction", "b")
    assert opened[0].closed, "the dead one is let go"


def test_a_closed_connection_is_replaced_too():
    # Covers: NFR-DEP-09
    resilient, opened, _ = wire(FakeConnection("a"), FakeConnection("b"))
    opened[0].closed = True
    assert resilient.cursor() == ("cursor", "b")


def test_one_idle_long_enough_to_have_been_dropped_is_checked_first():
    # Covers: NFR-DEP-09
    resilient, opened, clock = wire(FakeConnection("a", dies_on_ping=True), FakeConnection("b"))
    clock.now += PING_AFTER_SECONDS + 1
    # The server restarted while nobody was asking: the ping finds out, and the
    # caller never sees the failure.
    assert resilient.cursor() == ("cursor", "b")
    assert opened[0].queries == ["SELECT 1"]


def test_a_busy_connection_is_not_pinged_on_every_call():
    # Covers: NFR-DEP-09
    resilient, opened, clock = wire(FakeConnection("a"))
    for _ in range(5):
        clock.now += 1
        resilient.cursor()
    assert opened[0].queries == []


def test_the_row_format_survives_a_replacement():
    # Covers: NFR-DEP-09
    resilient, opened, _ = wire(FakeConnection("a"), FakeConnection("b"))
    marker = object()
    resilient.row_factory = marker
    assert opened[0].row_factory is marker
    opened[0].broken = True
    resilient.cursor()
    assert opened[1].row_factory is marker


def test_starting_without_a_database_fails_at_once_rather_than_later():
    # Covers: NFR-DEP-09
    def refuse():
        raise psycopg.OperationalError("connection refused")

    with pytest.raises(psycopg.OperationalError):
        ResilientConnection(refuse)
