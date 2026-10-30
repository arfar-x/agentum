"""Turns the bearer of a request into a Principal (FR-ATH). Provider-neutral."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from agent_gateway.application.identity_ports import (
    AuditLog,
    DirectoryStore,
    DirectorySync,
    Identity,
    KeyService,
    TokenVerifier,
)
from agent_gateway.domain.identity import (
    ISSUED_KEY_PREFIX,
    READ_ONLY_SCOPE,
    AuthError,
    KeyInfo,
    KeyScope,
    Principal,
)

log = logging.getLogger("agent_gateway.auth")

MAX_BEARER_BYTES = 8 * 1024
ON_DEMAND_GAP = timedelta(seconds=30)
FIRST_USE_WINDOW = timedelta(hours=24)
FIRST_USE_MAX_ENTRIES = 10_000
RETRY_AFTER = 30


class Authenticator:
    def __init__(self, keys: KeyService, tokens: TokenVerifier, directory: DirectoryStore,
                 sync: DirectorySync, audit: AuditLog, *, issue_keys: bool, max_stale: timedelta,
                 now: Callable[[], datetime], model_key_login: str = "read-only") -> None:
        self._keys, self._tokens, self._directory, self._sync = keys, tokens, directory, sync
        self._audit, self._issue_keys, self._max_stale, self._now = audit, issue_keys, max_stale, now
        self._model_key_login = model_key_login
        self._first_use: dict[tuple[str, str], datetime] = {}

    async def authenticate(self, bearer: str | None, *, path: str = "") -> Principal:
        actor = "anonymous"
        try:
            if not bearer or not bearer.strip():
                raise AuthError("no_bearer")
            bearer = bearer.strip()
            if len(bearer.encode()) > MAX_BEARER_BYTES:
                raise AuthError("malformed")
            info = self._recognised(await self._keys.resolve_all(bearer))
            if info is not None:
                actor = info.owner
                return await self._key_caller(info)
            return await self._token_caller(bearer)
        except AuthError as exc:
            await self._deny(exc.code, path, actor)
            raise

    # ---- a registered or issued key

    def _recognised(self, found: list[KeyInfo]) -> KeyInfo | None:
        """The one key this bearer identifies a person by, or None (then it may still be a token).

        An issued key is unique and always identifies its owner. A model key identifies a person
        only while `GATEWAY_MODEL_KEY_LOGIN` allows it and exactly one owner has it registered."""
        for info in found:
            if info.kind == "issued":
                return info
        if not found or self._model_key_login == "off":
            return None
        if len({i.owner for i in found}) > 1:
            raise AuthError("model_key_shared")
        return found[0]

    def _scope_of(self, info: KeyInfo) -> KeyScope:
        if info.kind == "upstream":
            return READ_ONLY_SCOPE if self._model_key_login == "read-only" else KeyScope()
        return info.scope

    async def _key_caller(self, info: KeyInfo) -> Principal:
        entry = await self._directory.lookup(info.owner)
        if entry is None or self._stale(entry.synced_at):
            await self._sync.refresh_if_due(ON_DEMAND_GAP)
            entry = await self._directory.lookup(info.owner)
        if entry is None:
            newest = await self._directory.newest_sync()
            if newest is None or self._stale(newest):
                raise AuthError("directory_stale", 503, RETRY_AFTER)
            raise AuthError("unknown_person")
        if self._stale(entry.synced_at):
            raise AuthError("directory_stale", 503, RETRY_AFTER)
        if not entry.enabled:
            raise AuthError("person_disabled")
        return Principal(entry.user_id, entry.username, entry.groups, entry.roles, entry.kind,
                         key_id=info.id, scope=self._scope_of(info))

    # ---- a token

    async def _token_caller(self, bearer: str) -> Principal:
        try:
            identity = await self._tokens.verify(bearer)
        except AuthError as exc:
            if bearer.startswith(ISSUED_KEY_PREFIX) and not self._issue_keys:
                raise AuthError("issuing_disabled") from None
            if bearer.count(".") != 2 and exc.code in ("malformed", "bad_signature", "bad_algorithm"):
                raise AuthError("unknown_key") from None
            raise
        entry = await self._directory.lookup(identity.sub)
        if entry is None:
            await self._sync.refresh_if_due(ON_DEMAND_GAP)
            entry = await self._directory.lookup(identity.sub)
        if entry is not None and not entry.enabled and not self._stale(entry.synced_at):
            raise AuthError("person_disabled")
        await self._note_first_use(identity)
        return Principal(identity.sub, identity.username, identity.groups, identity.roles, identity.kind)

    def _stale(self, synced_at: datetime) -> bool:
        return self._now() - synced_at > self._max_stale

    # ---- audit (best effort: a failure here never changes the outcome)

    async def _deny(self, code: str, path: str, actor: str) -> None:
        try:
            await self._audit.record(actor, "auth.denied", code, {"path": path, "reason": code})
        except Exception:
            log.warning("could not audit a refusal")

    async def _note_first_use(self, identity: Identity) -> None:
        if not identity.client:
            return
        now, key = self._now(), (identity.sub, identity.client)
        seen = self._first_use.get(key)
        if seen is not None and now - seen < FIRST_USE_WINDOW:
            return
        if len(self._first_use) >= FIRST_USE_MAX_ENTRIES:
            self._first_use = {k: v for k, v in self._first_use.items() if now - v < FIRST_USE_WINDOW}
        self._first_use[key] = now
        try:
            await self._audit.record(identity.sub, "auth.first_use", identity.client, {})
        except Exception:
            log.warning("could not audit a first use")
