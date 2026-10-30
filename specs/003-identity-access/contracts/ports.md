# Contract: ports and shared types

The contract every unit follows verbatim. Names, signatures and error types are fixed here so units
built in parallel meet in the middle. Gateway code is under `agent-gateway/src/agent_gateway/`.
`domain/` and `application/` import no I/O (`tests/test_boundaries.py`).

## 1. Domain types: `domain/identity.py` (unit U1)

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

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
```

Pure helpers in the same module (also U1): `valid_group(name) -> bool` (section 2 of the data
model; roles use the same rule via `valid_name`), `clean_groups(iterable)` and `clean_roles(iterable)
-> tuple[str, ...]` (drop invalid names and duplicates, keep order), `is_active(info: KeyInfo, now) -> bool` (not revoked, not expired).

## 2. Errors: `domain/identity.py`

```python
class AuthError(Exception):
    """The caller is refused. `code` is an audit reason code; `status` is 401 or 503."""
    code: str
    status: int = 401
    retry_after: int | None = None      # seconds, for 503

class KeyIssuingDisabled(Exception): ...    # GATEWAY_ISSUE_KEYS is off
class NoUpstreamKey(Exception): ...         # the person registered no model key (there is no shared fallback)
```

`AuthError.code` is one of the `auth.denied` reason codes in the data model. The HTTP layer maps:
401 `invalid_api_key` (OpenAI error shape, `"type": "invalid_request_error"`), 503
`identity_unavailable` with `Retry-After`, 403 `upstream_key_required` for `NoUpstreamKey`.

## 3. Ports: `application/identity_ports.py` (unit U1)

All methods are `async`. Two groups of ports:

- **The identity-provider bundle** (3.1): provider-neutral, implemented once per provider under
  `adapters/outbound/identity/<provider>/`. Only the Keycloak adapter exists. No provider name,
  endpoint shape or claim name appears outside that adapter.
- **Gateway stores and services** (3.2): Postgres adapters in `adapters/outbound/postgres_store.py`
  and in-memory fakes in `tests/application/fakes.py`.

### 3.1 The identity-provider bundle

```python
@dataclass(frozen=True)
class Identity:                      # who a verified token or a completed login says someone is
    sub: str                         # the provider's stable account id
    username: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    kind: Literal["user", "service"] = "user"

@dataclass(frozen=True)
class Account:                       # one row of the provider's user list
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

class DirectorySource(Protocol):
    async def fetch_all(self) -> list[Account]:
        """Every account, enabled or not, with effective groups and roles. All or nothing: raises
        if any part cannot be read; never returns a partial list."""

class PortalLogin(Protocol):
    def authorize_url(self, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str: ...
    async def complete(self, code: str, code_verifier: str, redirect_uri: str, nonce: str) -> Identity:
        """Exchange the code, verify the ID token (including the nonce), return the identity.
        Raises AuthError."""
```

Out of the interface on purpose: creating or deleting accounts, assigning groups or roles, setting
passwords, MFA, and any other account management. They stay in the provider's own admin UI. The
gateway never calls a write endpoint of the provider.

The provider is chosen by `IDENTITY_PROVIDER` (only `keycloak` is implemented) in the composition
root, which builds the three objects from the adapter's own settings. The sync job
(`application/directory_sync.py`, unit U2) is provider-neutral: it calls
`DirectorySource.fetch_all()` and `DirectoryStore.replace_all(...)`.

A provider contract test suite every adapter must pass is specified in
[`provider-contract-tests.md`](provider-contract-tests.md).

### 3.2 Stores and services

```python
class DirectoryStore(Protocol):
    async def lookup(self, user_id: str) -> DirectoryEntry | None: ...
    async def replace_all(self, accounts: Sequence[Account], synced_at: datetime) -> None:
        """One transaction: upsert every account, delete the rest. All rows get `synced_at`."""
    async def newest_sync(self) -> datetime | None: ...

class DirectorySync(Protocol):       # use case, application/directory_sync.py
    async def refresh(self) -> None:
        """fetch_all then replace_all. On any failure raises and leaves the copy unchanged; audits
        one event per failure streak."""
    async def refresh_if_due(self, min_gap: timedelta) -> None: ...   # on-demand, 30 s floor

class KeyService(Protocol):
    async def issue(self, owner: str, label: str, scope: KeyScope,
                    expires_at: datetime | None) -> tuple[str, KeyInfo]:
        """Raises KeyIssuingDisabled when GATEWAY_ISSUE_KEYS is off. The secret is returned once."""
    async def register_upstream(self, owner: str, label: str, secret: str) -> KeyInfo:
        """Stores hash + encrypted secret, kind='upstream'. Revokes the owner's previous active
        upstream key in the same transaction. Several people may register the same string."""
    async def resolve(self, bearer: str) -> KeyInfo | None:
        """Active key (not revoked, not expired) whose hash matches, any kind. Touches last_used_at
        (at most once a minute). For kind='issued' returns None when issuing is disabled."""
    async def upstream_key(self, owner: str) -> str | None:
        """Decrypted active upstream key of the owner, or None."""
    async def list_for(self, owner: str) -> list[KeyInfo]: ...
    async def revoke(self, owner: str | None, key_id: str) -> bool:
        """owner=None means an operator (any key). Returns False when nothing matched."""

class CredentialStore(Protocol):            # extends the existing store port
    async def list_vars(self, user: str, server: str) -> list[str]: ...    # names, never values
    async def delete_var(self, user: str, server: str, var: str) -> bool: ...
    # existing: get / set / delete(user, server)

class AuditLog(Protocol):
    async def record(self, actor: str, action: str, target: str = "",
                     detail: dict | None = None) -> None:
        """Folds an identical (actor, action, target) within 60 s into the earlier row."""
    async def recent(self, limit: int = 50, *, actor: str | None = None,
                     action: str | None = None, since: datetime | None = None) -> list[AuditEvent]: ...
    async def prune(self, older_than: datetime) -> int: ...
```

Also in U1: `OwnerPurge.purge(owner) -> dict[str, int]` on the store (deletes keys, credentials,
threads, pending turns, responses of one owner; returns counts) for FR-OPS-08.

## 4. Authenticator: `application/authenticate.py` (unit U2)

```python
class Authenticator:
    def __init__(self, keys: KeyService, tokens: TokenVerifier, directory: DirectoryStore,
                 sync: DirectorySync, audit: AuditLog, *, issue_keys: bool, max_stale: timedelta, now: Callable[[], datetime]): ...
    async def authenticate(self, bearer: str | None) -> Principal:
        """Order: (1) KeyService.resolve(bearer); (2) TokenVerifier.verify(bearer).
        Key callers: directory entry required, enabled, fresh -> Principal from it (groups and roles
        from the copy). Token callers: Principal from the Identity the verifier returned.
        Token callers: if a fresh row says enabled=False -> AuthError('person_disabled').
        Writes auth.denied audit events (best effort)."""
```

The HTTP adapter (`adapters/inbound/openai_http/auth.py`) calls it once per request and puts the
`Principal` on `request.state.principal`. `TurnRequest` gains `principal: Principal`; the old
`user_id` field stays as a read-only property returning `principal.user_id` so existing code and
tests keep working.

## 5. Authorisation: `domain/access.py` (unit U3)

Rules use both groups and roles (any-of): data model, section 9.

```python
def can_use(principal: Principal, agent: AgentDefinition) -> bool
def can_write(principal: Principal, agent: AgentDefinition) -> bool     # includes principal.scope.write
def visible_agents(principal: Principal, agents: Mapping[str, AgentDefinition]) -> list[AgentDefinition]
```

Rules are the decision table in the data model, section 9. `AgentDefinition.access:
AgentAccess | None` where `AgentAccess(groups, roles, write_groups, write_roles)`, each `tuple[str, ...] | None`.

## 6. Upstream key: `application/ports.py` (unit U5)

```python
class UpstreamCredentials(Protocol):
    async def for_principal(self, principal: Principal) -> str:
        """The person's registered upstream key, else raises NoUpstreamKey."""
```

The upstream LLM client takes the resolved key per call (`complete(..., api_key=...)`), never from
construction time. `openai_http/proxy.py` sets `Authorization: Bearer <resolved>` and drops the
caller's own header.

## 7. Tool gateway identity: `application/ports.py` (unit U7)

```python
class ToolGateway(Protocol):
    async def call(self, server: str, tool: str, args: dict, *, user: str,
                   credentials: Mapping[str, str], identity: Principal | None = None) -> ToolResult: ...
```

`identity` is used only to build `X-KB-Groups`/`X-KB-Trust` for servers flagged `forward_identity`.
`KbRetriever.search(...)` takes the same optional `identity`.

## 8. Knowledge base: caller (unit U6)

```python
@dataclass(frozen=True)
class Caller:
    groups: frozenset[str] = frozenset()      # empty = no group information
```

`SearchCatalog.run(..., caller: Caller = Caller())` and `GetEntry.run(..., caller: Caller = Caller())`.
`CatalogStore.search` and `.get`/`.get_many` take `groups: Collection[str]` and apply the predicate
in the data model ("SQL" section). The MCP adapter builds `Caller` from the request headers
(`contracts/headers-and-env.md`).

## 9. Bot (unit U8): ports

Described in generic words in [`bot-requirements.md`](bot-requirements.md); this repository does not
import the bot.
