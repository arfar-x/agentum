"""Ports for identity and access (specs/003-identity-access/contracts/ports.md, section 3).

Two groups. The identity-provider bundle is provider-neutral and implemented once per provider;
no provider name, endpoint shape or claim name appears in this file. The gateway's own stores and
services have Postgres adapters and in-memory fakes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from agent_gateway.domain.identity import AuditEvent, DirectoryEntry, KeyInfo, KeyScope

# ---- 3.1 The identity-provider bundle


@dataclass(frozen=True)
class Identity:
    """Who a verified token or a completed login says someone is."""

    sub: str                         # the provider's stable account id
    username: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    kind: Literal["user", "service"] = "user"
    client: str = ""                 # the client the token was issued to, when the provider says


@dataclass(frozen=True)
class Account:
    """One row of the provider's user list."""

    sub: str
    username: str
    enabled: bool
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    kind: Literal["user", "service"] = "user"


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Identity:
        """Check signature, issuer, audience, expiry (60 s leeway), algorithm. Raises AuthError.
        Group and role names are cleaned with clean_groups / clean_roles."""
        ...


class DirectorySource(Protocol):
    async def fetch_all(self) -> list[Account]:
        """Every account, enabled or not, with effective groups and roles. All or nothing: raises
        if any part cannot be read; never returns a partial list."""
        ...


class PortalLogin(Protocol):
    def authorize_url(self, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def complete(self, code: str, code_verifier: str, redirect_uri: str, nonce: str) -> Identity:
        """Exchange the code, verify the ID token (including the nonce), return the identity.
        Raises AuthError."""
        ...


# ---- 3.2 Stores and services


class DirectoryStore(Protocol):
    async def lookup(self, user_id: str) -> DirectoryEntry | None: ...

    async def replace_all(self, accounts: Sequence[Account], synced_at: datetime) -> None:
        """One transaction: upsert every account, delete the rest. All rows get `synced_at`."""
        ...

    async def newest_sync(self) -> datetime | None: ...

    async def list_all(self) -> list[DirectoryEntry]:
        """Every stored entry (operator listing)."""
        ...


class DirectorySync(Protocol):
    async def refresh(self) -> None:
        """fetch_all then replace_all. On any failure raises and leaves the copy unchanged; audits
        one event per failure streak."""
        ...

    async def refresh_if_due(self, min_gap: timedelta) -> None: ...


class KeyService(Protocol):
    async def issue(self, owner: str, label: str, scope: KeyScope, expires_at: datetime | None,
                    *, actor: str | None = None) -> tuple[str, KeyInfo]:
        """Raises KeyIssuingDisabled when GATEWAY_ISSUE_KEYS is off. The secret is returned once.
        `actor` is who is acting for the audit log; it defaults to the owner."""
        ...

    async def register_upstream(self, owner: str, label: str, secret: str,
                                *, actor: str | None = None) -> KeyInfo:
        """Stores hash + encrypted secret, kind='upstream'. Revokes the owner's previous active
        upstream key in the same transaction. Raises DuplicateKey."""
        ...

    async def resolve(self, bearer: str) -> KeyInfo | None:
        """Active key (not revoked, not expired) whose hash matches, any kind. Touches last_used_at
        (at most once a minute). For kind='issued' returns None when issuing is disabled."""
        ...

    async def upstream_key(self, owner: str) -> str | None:
        """Decrypted active upstream key of the owner, or None."""
        ...

    async def list_for(self, owner: str) -> list[KeyInfo]: ...

    async def revoke(self, owner: str | None, key_id: str, *, actor: str | None = None) -> bool:
        """owner=None means an operator (any key). Returns False when nothing matched."""
        ...


class AuditLog(Protocol):
    async def record(self, actor: str, action: str, target: str = "",
                     detail: dict | None = None) -> None:
        """Folds an identical (actor, action, target) within 60 s into the earlier row."""
        ...

    async def recent(self, limit: int = 50, *, actor: str | None = None,
                     action: str | None = None, since: datetime | None = None) -> list[AuditEvent]: ...

    async def prune(self, older_than: datetime) -> int: ...


class OwnerPurge(Protocol):
    async def purge(self, owner: str) -> dict[str, int]:
        """Delete keys, credentials, threads, pending turns and responses of one owner; return the
        counts. Records `owner.purge` in the same transaction."""
        ...


# ---- What KeyService needs from storage (implemented by the Postgres store and by a fake)


@dataclass(frozen=True)
class NewKey:
    owner: str
    kind: Literal["issued", "upstream"]
    label: str
    prefix: str
    key_hash: str
    secret_enc: bytes | None
    scope: KeyScope
    expires_at: datetime | None


class KeyRecords(Protocol):
    async def add_key(self, new: NewKey, *, actor: str) -> tuple[KeyInfo, str | None]:
        """Insert the key and its audit entry in one transaction. For `upstream`, the owner's
        previous active key is revoked (secret wiped) first; its id is returned as the second
        value. Raises DuplicateKey when the hash is active already."""
        ...

    async def find_active(self, key_hash: str, now: datetime) -> KeyInfo | None: ...
    async def touch(self, key_id: str, now: datetime) -> None: ...
    async def upstream_secret(self, owner: str) -> bytes | None: ...
    async def list_for(self, owner: str) -> list[KeyInfo]: ...

    async def revoke(self, owner: str | None, key_id: str, *, actor: str, by: str) -> bool:
        """Revoke (wiping an upstream secret) and write the audit entry in one transaction."""
        ...


class SecretBox(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...
