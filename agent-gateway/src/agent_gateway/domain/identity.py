"""Who someone is: the principal, keys, the directory copy, audit events and their rules.

Pure types and helpers. Nothing here knows which identity provider is in use.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

MAX_NAME_LENGTH = 128
ISSUED_KEY_PREFIX = "agk_"


@dataclass(frozen=True)
class KeyScope:
    agents: tuple[str, ...] = ()   # () = all permitted agents
    write: bool = True


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    kind: Literal["user", "service"] = "user"
    key_id: str | None = None
    scope: KeyScope = KeyScope()


@dataclass(frozen=True)
class DirectoryEntry:               # the stored copy of an Account
    user_id: str
    username: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    enabled: bool
    synced_at: datetime            # timezone-aware
    kind: Literal["user", "service"] = "user"


@dataclass(frozen=True)
class KeyInfo:                      # never carries the secret
    id: str
    owner: str
    kind: Literal["issued", "upstream"]
    label: str
    prefix: str
    scope: KeyScope
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True)
class AuditEvent:
    id: int
    ts: datetime
    last_ts: datetime
    actor: str
    action: str
    target: str
    detail: dict
    count: int


class AuthError(Exception):
    """The caller is refused. `code` is an audit reason code; `status` is 401 or 503."""

    def __init__(self, code: str, status: int = 401, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after


class KeyIssuingDisabled(Exception):
    """GATEWAY_ISSUE_KEYS is off."""


class NoUpstreamKey(Exception):
    """The person has no registered model key (there is no shared fallback key)."""


MODEL_KEY_LOGIN_MODES = ("off", "read-only", "full")
READ_ONLY_SCOPE = KeyScope(write=False)


def valid_name(name: object) -> bool:
    """A group or role name: non-empty after strip, at most 128 characters, no comma, no control character."""
    return (
        isinstance(name, str)
        and bool(name.strip())
        and len(name) <= MAX_NAME_LENGTH
        and "," not in name
        and not any(unicodedata.category(c) == "Cc" for c in name)
    )


def valid_group(name: object) -> bool:
    return valid_name(name)


def _clean(names: Iterable[object]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for name in names:
        if valid_name(name):
            seen.setdefault(name, None)      # type: ignore[arg-type]
    return tuple(seen)


def clean_groups(names: Iterable[object]) -> tuple[str, ...]:
    """Drop invalid names and duplicates, keep order."""
    return _clean(names)


def clean_roles(names: Iterable[object]) -> tuple[str, ...]:
    return _clean(names)


def is_active(info: KeyInfo, now: datetime) -> bool:
    """Not revoked and not expired."""
    return info.revoked_at is None and (info.expires_at is None or info.expires_at > now)


def hash_key(secret: str) -> str:
    """Hex SHA-256: how a key is recognised without being stored."""
    return hashlib.sha256(secret.encode()).hexdigest()


def scope_to_json(scope: KeyScope) -> dict[str, Any]:
    return {"agents": list(scope.agents), "write": scope.write}


def scope_from_json(data: dict[str, Any] | None) -> KeyScope:
    data = data or {}
    return KeyScope(agents=tuple(data.get("agents") or ()), write=bool(data.get("write", True)))
