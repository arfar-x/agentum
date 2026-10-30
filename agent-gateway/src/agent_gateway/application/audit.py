"""Audit rules that do not depend on where the log is kept: the vocabulary, folding, bounding."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from agent_gateway.domain.identity import AuditEvent, KeyInfo, scope_to_json

FOLD_WINDOW = timedelta(seconds=60)
MAX_DETAIL_BYTES = 2048

ACTIONS = frozenset({
    "key.register", "key.issue", "key.revoke", "cred.set", "cred.delete", "cred.test", "auth.denied",
    "auth.first_use", "directory.sync_failed", "directory.sync_recovered", "upstream.key_rejected",
    "owner.purge",
})

# Field names that must never carry a value into the log, whatever a caller passes.
_SECRET_FIELDS = frozenset({"token", "key", "secret", "password", "authorization", "value", "credential", "api_key"})


def can_fold(previous: AuditEvent, actor: str, action: str, target: str, now: datetime) -> bool:
    """A repeat of the same (actor, action, target) within 60 seconds of the earlier one's last time."""
    return (previous.actor, previous.action, previous.target) == (actor, action, target) \
        and now - previous.last_ts <= FOLD_WINDOW


def clean_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Secret-free and at most 2 KiB serialised. Longer detail is cut and marked."""
    safe = {k: ("[removed]" if k.lower() in _SECRET_FIELDS else v) for k, v in (detail or {}).items()}
    text = json.dumps(safe, default=str, ensure_ascii=False)
    if len(text.encode()) <= MAX_DETAIL_BYTES:
        return json.loads(text)
    return {"truncated": text.encode()[:MAX_DETAIL_BYTES - 64].decode(errors="ignore")}


def key_added_event(info: KeyInfo, replaced: str | None) -> tuple[str, str, dict[str, Any]]:
    """(action, target, detail) for a registered or issued key."""
    if info.kind == "upstream":
        detail: dict[str, Any] = {"label": info.label, "prefix": info.prefix}
        if replaced:
            detail["replaced"] = replaced
        return "key.register", info.id, detail
    return "key.issue", info.id, {
        "label": info.label, "prefix": info.prefix, "scope": scope_to_json(info.scope),
        "expires_at": info.expires_at.isoformat() if info.expires_at else None}


def key_revoked_event(info: KeyInfo, by: str) -> tuple[str, str, dict[str, Any]]:
    return "key.revoke", info.id, {"by": by}


async def prune_audit(audit, retention_days: int, now: datetime) -> int:
    """Delete entries whose last occurrence is older than the retention (FR-AUD-06)."""
    return await audit.prune(now - timedelta(days=retention_days))
