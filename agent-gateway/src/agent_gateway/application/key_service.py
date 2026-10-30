"""Keys: issued gateway keys and a person's own model key. Storage sits behind ``KeyRecords``."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from agent_gateway.application.identity_ports import KeyRecords, NewKey, SecretBox
from agent_gateway.domain.identity import (
    ISSUED_KEY_PREFIX,
    KeyInfo,
    KeyIssuingDisabled,
    KeyScope,
    hash_key,
    is_active,
)

TOUCH_EVERY = timedelta(minutes=1)
ISSUED_KEY_BYTES = 32


def issued_prefix(secret: str) -> str:
    """First 8 characters of an issued key (`agk_` plus 4): recognisable, not a secret."""
    return secret[:8]


def upstream_prefix(secret: str) -> str:
    """Last 4 characters of a model key. Never the first: they can identify the provider."""
    return secret[-4:]


class KeyManager:
    """Implements ``KeyService``."""

    def __init__(self, records: KeyRecords, box: SecretBox, *, issue_keys: bool,
                 now: Callable[[], datetime]) -> None:
        self._records, self._box, self._issue_keys, self._now = records, box, issue_keys, now

    async def issue(self, owner: str, label: str, scope: KeyScope, expires_at: datetime | None,
                    *, actor: str | None = None) -> tuple[str, KeyInfo]:
        if not self._issue_keys:
            raise KeyIssuingDisabled("issuing gateway keys is disabled (GATEWAY_ISSUE_KEYS is off)")
        secret = ISSUED_KEY_PREFIX + secrets.token_urlsafe(ISSUED_KEY_BYTES)
        new = NewKey(owner, "issued", label, issued_prefix(secret), hash_key(secret), None, scope, expires_at)
        info, _ = await self._records.add_key(new, actor=actor or owner)
        return secret, info

    async def register_upstream(self, owner: str, label: str, secret: str,
                                *, actor: str | None = None) -> KeyInfo:
        secret = secret.strip()
        if not secret:
            raise ValueError("the model key is empty")
        new = NewKey(owner, "upstream", label, upstream_prefix(secret), hash_key(secret),
                     self._box.encrypt(secret.encode()), KeyScope(), None)
        info, _ = await self._records.add_key(new, actor=actor or owner)
        return info

    async def resolve(self, bearer: str) -> KeyInfo | None:
        now = self._now()
        info = await self._records.find_active(hash_key(bearer), now)
        if info is None or not is_active(info, now):
            return None
        if info.kind == "issued" and not self._issue_keys:
            return None
        if info.last_used_at is None or now - info.last_used_at >= TOUCH_EVERY:
            await self._records.touch(info.id, now)
        return info

    async def upstream_key(self, owner: str) -> str | None:
        stored = await self._records.upstream_secret(owner)
        return self._box.decrypt(stored).decode() if stored else None

    async def list_for(self, owner: str) -> list[KeyInfo]:
        return await self._records.list_for(owner)

    async def revoke(self, owner: str | None, key_id: str, *, actor: str | None = None) -> bool:
        try:
            uuid.UUID(key_id)
        except ValueError:
            return False
        by = "operator" if owner is None else "owner"
        return await self._records.revoke(owner, key_id, actor=actor or owner or "cli", by=by)
