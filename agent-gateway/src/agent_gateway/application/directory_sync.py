"""Keeps the gateway's copy of the identity provider's people current. Provider-neutral."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from agent_gateway.application.identity_ports import AuditLog, DirectorySource, DirectoryStore

log = logging.getLogger("agent_gateway.directory")

MIN_ON_DEMAND_GAP = timedelta(seconds=30)
_ERROR_CHARS = 200


class DirectorySync:
    """Implements ``DirectorySync``: fetch everything, replace the copy in one step, audit failure streaks."""

    def __init__(self, source: DirectorySource, store: DirectoryStore, audit: AuditLog,
                 *, now: Callable[[], datetime]) -> None:
        self._source, self._store, self._audit, self._now = source, store, audit, now
        self._failing = False
        self._last_attempt: datetime | None = None

    async def refresh(self) -> None:
        self._last_attempt = self._now()
        try:
            accounts = await self._source.fetch_all()
            if not accounts:          # a provider always has at least its own service account; empty means a bad read
                raise ValueError("the identity provider returned no accounts")
            await self._store.replace_all(accounts, self._now())
        except Exception as exc:
            if not self._failing:
                self._failing = True
                text = f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]
                await self._record("directory.sync_failed", {"error": text})
            raise
        if self._failing:
            self._failing = False
            await self._record("directory.sync_recovered", {})

    async def refresh_if_due(self, min_gap: timedelta = MIN_ON_DEMAND_GAP) -> None:
        """An on-demand refresh, at most once per ``min_gap``. Never raises: the caller is serving a request."""
        now = self._now()
        if self._last_attempt is not None and now - self._last_attempt < min_gap:
            return
        try:
            await self.refresh()
        except Exception:
            log.warning("on-demand directory refresh failed")

    async def _record(self, action: str, detail: dict) -> None:
        try:
            await self._audit.record("system", action, "", detail)
        except Exception:
            log.warning("could not audit %s", action)
