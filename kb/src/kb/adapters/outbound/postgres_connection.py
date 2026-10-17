"""A Postgres connection that outlives a database restart.

Every process here holds one connection for its whole life -- the scheduler for
days, the console and the MCP server for as long as they are up. A plain psycopg
connection that loses its server stays lost: every later call raises
`the connection is lost` until somebody restarts the process by hand. A database
restart, a failover or an idle-connection timeout in front of it is routine, so
the connection is replaced instead.

What this does, and what it does not:

- A connection that psycopg already knows is gone (`closed`, `broken`) is
  replaced before the next use.
- One that has been idle for a while is pinged first, because "the server
  restarted while nobody was asking" is the common case and psycopg cannot know
  it until it tries. The first request after a restart then just works.
- A connection that dies *during* a call raises, as it must: a transaction that
  was cut in half is not something to retry blindly. The call after it
  reconnects.

autocommit is on (see `PostgresEntryStore.connect`), so there is never a
half-open transaction to carry over to the new connection.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("kb.db")

#: Idle this long and the connection is checked before it is trusted. Short on
#: purpose: a restart is over in well under a second, and a request that arrives
#: just after it should not be the one that finds out. The check is one
#: round trip, paid only after a pause -- the statements of one request, which
#: follow each other within milliseconds, are not checked again.
PING_AFTER_SECONDS = 0.25


class ResilientConnection:
    """The slice of `psycopg.Connection` the stores use, over a connection that is
    replaced when it dies. Anything else is delegated to the live connection."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        *,
        ping_after: float = PING_AFTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self._ping_after = ping_after
        self._clock = clock
        self._lock = threading.Lock()
        self._row_factory: Any = dict_row
        self._conn = self._open()  # fail now, at startup, if the database is not there
        self._last_used = self._clock()

    @classmethod
    def to(cls, dsn: str, *, connect_timeout: int = 5, **kwargs: Any) -> "ResilientConnection":
        # TCP keepalives, so a connection whose far end vanished without a word (a
        # failover, a firewall that dropped the flow) is noticed in about a
        # minute instead of blocking a request for ever.
        return cls(
            lambda: psycopg.connect(
                dsn, connect_timeout=connect_timeout, autocommit=True,
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
            ),
            **kwargs,
        )

    # -- keeping a live connection -----------------------------------------
    def _open(self) -> psycopg.Connection:
        conn = self._connect()
        conn.row_factory = self._row_factory
        return conn

    def _live(self) -> psycopg.Connection:
        with self._lock:
            idle = self._clock() - self._last_used
            self._last_used = self._clock()
            conn = self._conn
            status = getattr(getattr(conn, "info", None), "transaction_status", None)
            in_transaction = (
                psycopg.pq.TransactionStatus.INTRANS,
                psycopg.pq.TransactionStatus.ACTIVE,
                psycopg.pq.TransactionStatus.INERROR,
            )
            if status in in_transaction:
                # In the middle of a transaction: the next statement belongs to
                # it. Pinging would be a statement of its own, and replacing the
                # connection would move the rest of the transaction elsewhere.
                return conn
            lost = conn.closed or conn.broken
            if not lost and idle > self._ping_after:
                try:
                    conn.execute("SELECT 1")
                except psycopg.Error:
                    # Not only a dead socket: a server shutting down answers the
                    # ping with an error ("terminating connection due to
                    # administrator command") before the socket closes, and the
                    # connection does not yet know it is finished. A ping that
                    # fails means the connection is not to be used.
                    lost = True
            if lost:
                logger.warning("the database connection was lost; reconnecting")
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = conn = self._open()
                logger.info("reconnected to the database")
            return conn

    # -- what the stores use -------------------------------------------------
    def cursor(self, *args: Any, **kwargs: Any):
        return self._live().cursor(*args, **kwargs)

    def transaction(self, *args: Any, **kwargs: Any):
        return self._live().transaction(*args, **kwargs)

    def execute(self, *args: Any, **kwargs: Any):
        return self._live().execute(*args, **kwargs)

    @property
    def row_factory(self) -> Any:
        return self._row_factory

    @row_factory.setter
    def row_factory(self, factory: Any) -> None:
        # Remembered, so a replacement connection reads rows the same way.
        self._row_factory = factory
        self._conn.row_factory = factory

    @property
    def closed(self) -> bool:
        return self._conn.closed

    @property
    def broken(self) -> bool:
        return self._conn.broken

    def close(self) -> None:
        self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)
