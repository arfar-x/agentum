# Implementation Plan: Identity and access

**Branch**: `spec/identity-access` (design); each unit builds on its own `feat/...` branch | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-identity-access/spec.md`

## Summary

One identity, the Keycloak account id, replaces the gateway's hand-made user names. A user-management
layer inside `agent-gateway` (identity types, ports, adapters, a small portal) is used by every
client. The Telegram bot links through Keycloak's device sign-in and calls the gateway as the person.
Each person's own model key is stored encrypted and used for all of their upstream calls. The
knowledge base gets per-source group visibility, enforced in the catalog and fed by headers the
gateway sends under a shared secret.

Nothing new runs as a service. The gateway gains an in-process directory sync task and a mounted
web sub-application; Keycloak is the existing server with a new realm.

## Technical Context

**Language/Version**: Python 3.12 (`agent-gateway/`, `kb/`, the bot); Keycloak 26.x for the realm
(behind a provider-neutral interface in the gateway).

**Primary Dependencies**: existing: FastAPI, httpx, psycopg 3, cryptography (Fernet), pydantic v2,
PyYAML, LangGraph. New in the gateway: `pyjwt[crypto]` (token verification), `jinja2` (portal
templates), `python-multipart` (form posts). The bot gains no new dependency (httpx is present).
The kb gains none.

**Storage**: gateway Postgres (migration `005_identity.sql`; `006` reserved), kb Postgres
(`005_source_visibility.sql`), bot Postgres on `gateway-data` (`002_oidc_links.sql`). No new
database, no Redis use beyond today's.

**Testing**: pytest in each module, no services for unit and application tests (in-memory fakes),
Postgres tests under `scripts/test-services.sh` (gateway), `make kb-test` (kb), the bot's
`scripts/test-services.sh`. Gates: each module's `tests/test_spec_coverage.py` and
`tests/test_boundaries.py`.

**Target Platform**: Linux containers on one host.

**Project Type**: three hexagonal modules plus stack wiring.

**Performance Goals**: authentication adds at most one indexed lookup (key hash) or one local
signature check (token) plus one primary-key lookup (directory row) per request. The directory sync
for under 200 people is one admin call per page of users plus one for groups, every 5 minutes.

**Constraints**: no secret in any response, log, audit row or model context; `domain/` and
`application/` import no I/O; the bot repository never names the stack; local branches only.

**Scale/Scope**: under 200 people, a handful of agents, a handful of kb sources.

## Constitution Check

| Principle | Check |
|---|---|
| I. Configuration over code | Nothing upstream is patched. LibreChat is untouched. The bot stays generic and its glue is in `integrations/`. The Keycloak realm is configuration (a template and a script). **Pass.** |
| II. Spec of record leads | Requirement rows are added to `agent-gateway/docs/spec.md` and `kb/docs/SPEC.md` with ids and status `planned` before any code; the bot's text is held in `contracts/bot-requirements.md` until U8 applies it in the bot repository (the submodule cannot be edited from this change). Units flip rows to `done` with `# Covers:` tests. **Pass, with the bot step deferred to U8 by necessity.** |
| III. Hexagonal core | New domain types and ports are in `domain/` and `application/` with fakes; JWT, HTTP, Postgres, Jinja and cookies live in adapters, and every provider-specific detail lives in one adapter package behind three ports. `test_boundaries.py` stays green. **Pass.** |
| IV. Secrets and internal services | New secrets only in env files (`.env.agent-gateway`, `kb/.env`). `mcp-kb` stays internal; it receives one more variable in an explicit list. `agent-gateway` stays off `backend`. The portal is on the gateway's existing published port, authenticated by its own session; no new port. **Pass.** |
| V. Least privilege | `mcp-kb` holds the trust secret but no source credential and no write role. `kb-admin` reads `source_visibility` only. The directory client has Keycloak read-only roles. The gateway's per-person model key is readable only by the code that calls the model. **Pass.** |
| No new infrastructure | No new service or store. **Pass.** |
| Pinned versions | Keycloak image tag pinned in the stack tooling (U9); new Python dependencies pinned in each `pyproject.toml`. **Pass.** |
| Declarative state | Agent access, kb source groups, tool credential declarations and the realm are files. **Pass.** |

One deliberate departure to record, not a violation: `kb`'s non-goal N3 ("not an access-control
system") is amended into "coarse, per-source group visibility only", in `kb/docs/SPEC.md`.

## Project Structure

### Documentation (this feature)

```text
specs/003-identity-access/
├── spec.md          # stories, scenarios, edge cases, success criteria
├── plan.md          # this file
├── research.md      # decisions, rejected alternatives, risks
├── data-model.md    # every entity, ER diagram, the three migrations
├── quickstart.md    # the end-to-end recipe
├── tasks.md         # units U1..U9, files, dependencies
└── contracts/
    ├── ports.md
    ├── provider-contract-tests.md
    ├── headers-and-env.md
    ├── http-portal.md
    ├── yaml-schemas.md
    ├── keycloak-realm.md
    └── bot-requirements.md
```

### Source code

```text
agent-gateway/
├── migrations/005_identity.sql
├── config/mcp-servers.yaml                      # credentials objects, test, forward_identity
├── config/agents/*.yaml                         # access examples
├── docs/spec.md                                 # FR-IDN/ATH/KEY/UPS/ENT/CRD/PRT/AUD/KBI/OPS/CFG rows
└── src/agent_gateway/
    ├── domain/identity.py                       # U1: Principal, KeyScope, DirectoryEntry, KeyInfo, errors, helpers
    ├── domain/access.py                         # U3: can_use, can_write, visible_agents
    ├── domain/agent.py, routing.py              # U3: AgentAccess; routing filter
    ├── application/identity_ports.py            # U1: the ports
    ├── application/authenticate.py              # U2: Authenticator
    ├── application/turn.py                      # U3, U5, U7: access checks, upstream key, identity forwarding
    ├── adapters/inbound/openai_http/{auth,models,proxy,app,chat,responses}.py   # U2, U3, U5
    ├── adapters/inbound/portal/                 # U4: routes, session, csrf, templates/, static/
    ├── adapters/inbound/cli.py                  # U1, U2: key, creds, audit, directory, owner commands
    ├── adapters/outbound/postgres_store.py      # U1: keys, snapshot, audit, credentials
    ├── application/directory_sync.py            # U2: provider-neutral sync job
    ├── adapters/outbound/identity/keycloak/     # U2: the only provider: verifier, directory source, portal login, settings
    ├── tests/providers/contract.py              # U2: the reusable provider contract tests
    ├── adapters/outbound/{upstream_llm,mcp_tools}.py   # U5, U7
    └── adapters/outbound/config_files.py        # U3, U4: new fields
kb/
├── migrations/005_source_visibility.sql
├── docs/{SPEC.md,CONFIGURATION.md,OPERATOR_GUIDE.md}
├── compose.yaml, .env.example                   # KB_TRUST_SECRET for mcp-kb
└── src/kb/{sources_config.py,config.py,domain,application,adapters}   # U6
telegram-bot-gateway/                            # U8, inside the submodule, generic wording only
integrations/keycloak/                           # U9: realm template, apply script
integrations/telegram-bot/                       # U9: wiring for device mode
scripts/identity-e2e.sh, docs/KEYCLOAK.md, docs/CONFIGURATION.md, CLAUDE.md   # U9
```

## Design notes

### Request path

```
client ──Bearer──▶ openai_http/auth ──▶ Authenticator
                      │                    1. KeyService.resolve(hash)       ─▶ key caller
                      │                    2. TokenVerifier.verify(jwt)      ─▶ Identity ─▶ token caller
                      │                    key caller: DirectoryStore.lookup(owner) fresh+enabled
                      │                    token caller: directory row says disabled? refuse
                      ▼
                   Principal ─▶ TurnRequest.principal
                      │
        models.py / routing ─▶ domain/access.can_use    (list, route, handoff, resume)
        turn.py build_toolset ─▶ domain/access.can_write (hide approval-gated tools)
        upstream call  ─▶ UpstreamCredentials.for_principal ─▶ Authorization to the model
        tool call to a forward_identity server ─▶ X-KB-Groups, X-KB-Trust
```

### The two ways to be a caller

| | Token caller | Key caller |
|---|---|---|
| Bearer | Keycloak access token | person's registered model key (or `agk_` key with the flag) |
| Groups from | the token's `groups` claim | the directory copy |
| Disabled person | refused after the next sync (token is valid until `exp` otherwise) | refused after the next sync |
| Keycloak down | works until `exp`; signing keys from cache | works until the copy is 15 minutes old, then 503 |
| Model key used | the person's registered key | that same key |

### Directory sync

An `asyncio` task started with the app: every `DIRECTORY_SYNC_SECONDS` call
`DirectorySource.fetch_all()` and replace the snapshot with `DirectoryStore.replace_all` in one
transaction. The Keycloak adapter's `fetch_all` gets a service-account token (`client_credentials`),
pages through `/admin/realms/{realm}/users` (briefRepresentation false, `max=100`), reads each
group's members (including subgroups by id, because nested groups are flattened to their leaf name)
and each account's effective realm and client roles, and returns `Account`s. None of that is known
outside the adapter. A failure at any step discards the sync, leaves the copy, and records one audit event per
failure streak. The first sync runs before the app accepts requests (bounded by a 30 s timeout; on
timeout the app starts and key callers get 503 until a sync succeeds). Users unknown to the copy
trigger an on-demand refresh at most once every 30 seconds.

### Portal

Separate router mounted at `/portal` in `create_app`, using the same stores through the same ports.
Sessions are signed cookies, so there is no session table and no shared state between processes.

### Data flow of the model key

Portal or `key register-llm` → `KeyService.register_upstream` (hash + Fernet) → `api_keys` row. At
request time `UpstreamCredentials.for_principal` decrypts it (one primary-key read) and the upstream
client puts it in the `Authorization` header for that call only. It is not placed in the turn
request, the thread, a pending turn, a log or the model context.

### Knowledge base

`kb-scheduler` upserts `source_visibility` from `sources.yaml` at the start of each run (and on a
configuration change). `SearchCatalog` and `GetEntry` take a `Caller`; the store applies the
visibility predicate in the query. `mcp-kb` builds the `Caller` from the headers only when
`X-KB-Trust` equals `KB_TRUST_SECRET`.

## How to add another identity provider

The gateway core knows three ports (contracts/ports.md, 3.1) and nothing else about identity. To add
a provider, for example `acme`:

1. Create `agent-gateway/src/agent_gateway/adapters/outbound/identity/acme/` with a verifier
   (`TokenVerifier`), a directory source (`DirectorySource`), a portal login (`PortalLogin`) and a
   settings class that reads only that provider's own environment variables and validates them.
2. Add `"acme"` to the table in the composition root that maps `IDENTITY_PROVIDER` to a factory
   returning the three objects.
3. Add `tests/providers/test_acme_contract.py` with `class TestAcme(IdentityProviderContract)`
   implementing `make_provider`; all cases in
   [`contracts/provider-contract-tests.md`](contracts/provider-contract-tests.md) must pass with no
   network.
4. Document the provider's setup next to its adapter and in the operator docs; add its settings to
   `.env.agent-gateway.example` under a clear heading.
5. Nothing else changes: not `domain/`, not `application/`, not the portal, not the authenticator,
   not the other adapters. If a change there seems needed, the port is missing something and the
   change is to `ports.md` and `FR-IDP-*` first.

Account management (creating users, roles, passwords, MFA) is never part of an adapter.

## Delivery order and parallelism

Wave 0 is this specification, reviewed before any code. Wave 1 (independent): U1, U6, U8, U9. Wave 2,
after merging `feat/idn-store` into the worktree: U2, U3, U4, U5, U7. Merge order: U1, U2, U5, U3,
U7/U6, U4, U8, U9; then the cross-unit end-to-end run ([`quickstart.md`](quickstart.md)). Details in
[`tasks.md`](tasks.md).

## Complexity Tracking

| Item | Why it is needed | Simpler alternative rejected because |
|---|---|---|
| Two bearer kinds (token and key) | Telegram and the portal can do the sign-in; scripts and clients that only hold a model key cannot | tokens only would strand every client that cannot run the sign-in |
| A directory copy in addition to token claims | key callers carry no groups; "keep working briefly" needs a local copy | asking Keycloak per request would make Keycloak an availability dependency of every call |
| A trust header pair into `mcp-kb` | `mcp-kb` is reachable from `backend` (LibreChat is there) and has no authentication | trusting the header unconditionally would let any container on `backend` claim any group |
