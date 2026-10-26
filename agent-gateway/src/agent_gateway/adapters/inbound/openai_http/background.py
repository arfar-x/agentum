"""Work that runs beside the server: the directory sync and audit pruning (FR-IDN-03, FR-AUD-06)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from agent_gateway.application.audit import prune_audit

log = logging.getLogger("agent_gateway.background")

FIRST_SYNC_TIMEOUT = 30
PRUNE_EVERY_SECONDS = 6 * 3600


async def first_sync(sync) -> None:
    """Sync once before serving, but never block startup for more than 30 seconds."""
    try:
        await asyncio.wait_for(sync.refresh(), FIRST_SYNC_TIMEOUT)
    except Exception as exc:
        log.warning("first directory sync did not finish (%s); key callers get 503 until one succeeds",
                    type(exc).__name__)


async def _every(seconds: int, work, name: str) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await work()
        except Exception as exc:
            log.warning("%s failed (%s); will retry", name, type(exc).__name__)


async def _prune(container) -> None:
    removed = await prune_audit(container.audit, container.audit_retention_days, datetime.now(timezone.utc))
    if removed:
        log.info("pruned %d audit entries", removed)


async def start(container) -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []
    if container.directory_sync is not None:
        await first_sync(container.directory_sync)
        tasks.append(asyncio.create_task(_every(container.sync_seconds, container.directory_sync.refresh,
                                                "directory sync")))
    if container.audit is not None:
        try:
            await _prune(container)
        except Exception as exc:
            log.warning("audit pruning failed (%s)", type(exc).__name__)
        tasks.append(asyncio.create_task(_every(PRUNE_EVERY_SECONDS, lambda: _prune(container), "audit pruning")))
    return tasks


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
