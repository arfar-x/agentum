"""What a signed-in person can do with their own tool credentials (portal, FR-PRT-07, FR-PRT-08).

Only variables a tool server declares can be stored (FR-CRD-08); values are never returned, except
for a variable the server declares as not secret. Every change is audited with the person as actor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from agent_gateway.application.identity_ports import AuditLog
from agent_gateway.application.models import CredentialDecl, ServerInfo
from agent_gateway.application.ports import ToolGateway

MAX_VALUE_CHARS = 4096
MAX_ERROR_CHARS = 300
MASK = "***"


class UndeclaredCredential(LookupError):
    """The server does not exist, or does not declare that variable."""


class InvalidCredential(ValueError):
    """The value cannot be stored; the message is safe to show."""


class CredentialRecords(Protocol):
    """The part of the credential store the portal uses. Changes and their audit entry are written
    in one transaction by the store, with ``actor`` as the actor."""

    async def get(self, user_id: str, server: str) -> dict[str, str]: ...
    async def set_credential(self, user_id: str, server: str, var: str, value: str, *, actor: str = "cli") -> None: ...
    async def delete_var(self, user: str, server: str, var: str, *, actor: str = "cli") -> bool: ...


@dataclass(frozen=True)
class CredentialView:
    decl: CredentialDecl
    is_set: bool
    shown_value: str | None = None      # only for a variable declared `secret: false`


@dataclass(frozen=True)
class ServerCard:
    server: str
    credentials: tuple[CredentialView, ...]
    can_test: bool


@dataclass(frozen=True)
class ProbeOutcome:
    ok: bool
    message: str


def check_declared(servers: Mapping[str, ServerInfo], server: str, var: str) -> CredentialDecl:
    info = servers.get(server)
    for decl in info.declared() if info else ():
        if decl.name == var:
            return decl
    raise UndeclaredCredential(f"{server}/{var}")


def clean_value(value: str) -> str:
    """A credential value becomes a request header: one line, printable, bounded."""
    value = value.strip()
    if not value:
        raise InvalidCredential("The value is empty.")
    if len(value) > MAX_VALUE_CHARS:
        raise InvalidCredential(f"The value is longer than {MAX_VALUE_CHARS} characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InvalidCredential("The value contains a line break or control character.")
    return value


def mask(text: str, secrets: Mapping[str, str] | list[str]) -> str:
    values = secrets.values() if isinstance(secrets, Mapping) else secrets
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(value, MASK)
    return text


class CredentialCenter:
    def __init__(self, servers: Mapping[str, ServerInfo], records: CredentialRecords, tools: ToolGateway,
                 audit: AuditLog) -> None:
        self._servers, self._records, self._tools, self._audit = servers, records, tools, audit

    def declaring_servers(self) -> list[str]:
        return [name for name, info in self._servers.items() if info.declared()]

    async def cards(self, user: str) -> list[ServerCard]:
        out = []
        for name in self.declaring_servers():
            info = self._servers[name]
            stored = await self._records.get(user, name)
            views = tuple(CredentialView(d, d.name in stored, None if d.secret else stored.get(d.name))
                          for d in info.declared())
            out.append(ServerCard(name, views, info.test is not None))
        return out

    async def set(self, user: str, server: str, var: str, value: str) -> None:
        check_declared(self._servers, server, var)
        await self._records.set_credential(user, server, var, clean_value(value), actor=user)

    async def delete(self, user: str, server: str, var: str) -> bool:
        check_declared(self._servers, server, var)
        return await self._records.delete_var(user, server, var, actor=user)

    async def test(self, user: str, server: str) -> ProbeOutcome:
        info = self._servers.get(server)
        if info is None or info.test is None:
            raise UndeclaredCredential(server)
        stored = await self._records.get(user, server)
        declared = {d.name for d in info.declared()}
        creds = {k: v for k, v in stored.items() if k in declared}
        missing = [d.name for d in info.declared() if d.required and d.name not in creds]
        if missing:
            return ProbeOutcome(False, "Set " + ", ".join(missing) + " first.")
        result = await self._tools.call(server, info.test.tool, dict(info.test.args), creds)
        ok = not result.is_error
        await self._audit.record(user, "cred.test", server, {"ok": ok})
        if ok:
            return ProbeOutcome(True, "The credentials work.")
        return ProbeOutcome(False, mask(result.text, creds)[:MAX_ERROR_CHARS] or "The test failed.")
