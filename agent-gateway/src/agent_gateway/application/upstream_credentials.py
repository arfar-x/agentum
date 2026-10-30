"""Which model key a call is made with: the person's own. There is no shared fallback."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_gateway.application.identity_ports import AuditLog, KeyService
from agent_gateway.application.models import UpstreamKey
from agent_gateway.domain.errors import UpstreamKeyRejected, UpstreamKeyRequired, UpstreamRejected
from agent_gateway.domain.identity import Principal

REFUSED = (401, 403)


class UpstreamKeys:
    """Implements ``UpstreamCredentials``."""

    def __init__(self, keys: KeyService, audit: AuditLog | None = None, portal_url: str = "") -> None:
        self._keys, self._audit, self._portal = keys, audit, portal_url.strip()

    def _where_to_register(self) -> str:
        cli = "`python -m agent_gateway key register-llm`"
        return f"the portal at {self._portal} or with {cli}" if self._portal else f"the portal or with {cli}"

    async def resolve(self, principal: Principal) -> UpstreamKey:
        personal = await self._keys.upstream_key(principal.user_id)
        if personal:
            return UpstreamKey(personal)
        raise UpstreamKeyRequired(f"No model key is registered for you. Register yours in {self._where_to_register()}.")

    async def for_principal(self, principal: Principal) -> str:
        return (await self.resolve(principal)).secret

    async def rejected(self, principal: Principal, key: UpstreamKey, status: int) -> UpstreamKeyRejected:
        if self._audit is not None:
            infos = await self._keys.list_for(principal.user_id)
            current = next((i.id for i in infos if i.kind == "upstream" and i.revoked_at is None), "")
            await self._audit.record(principal.user_id, "upstream.key_rejected", current, {"status": status})
        return UpstreamKeyRejected(f"The model provider refused your model key. Replace it in {self._where_to_register()}.")

    @asynccontextmanager
    async def use(self, principal: Principal) -> AsyncIterator[str]:
        key = await self.resolve(principal)
        try:
            yield key.secret
        except UpstreamRejected as exc:
            if exc.status in REFUSED:
                raise await self.rejected(principal, key, exc.status) from None
            raise
