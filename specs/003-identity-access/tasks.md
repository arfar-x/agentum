# Tasks: Identity and access

**Input**: [spec.md](spec.md), [plan.md](plan.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Rules for every unit**

- Work on the unit's branch, cut from the current merge target. **Commit only; never push, never
  open a pull request.** Conventional Commits, describing what and why, nothing about the session.
- Flip the unit's requirement rows from `planned` to `done` in the module spec **in the same
  commit** as the tests that claim them (`# Covers: FR-...`). The module's spec-coverage and
  boundary gates stay green.
- Follow the shared contract in [`contracts/ports.md`](contracts/ports.md) verbatim. A needed change
  to a contract is a change to this directory first.
- Run `Skill code-review` on the diff before the final commit, then the module's tests, then the
  unit's part of [`quickstart.md`](quickstart.md).
- End the report with a single line: `BRANCH: <name> @ <short sha>` (or `BRANCH: none, <reason>`).

## Waves and dependencies

```
Wave 0:  U0 (this spec)  ──review──┐
Wave 1:  U1   U6   U8   U9  (independent of each other)
Wave 2:  merge feat/idn-store (U1) into the worktree, then  U2  U3  U4  U5  U7
Merge order: U1 → U2 → U5 → U3 → U7 / U6 → U4 → U8 → U9, then the cross-unit end-to-end run
```

| Unit | Branch | Needs | Rows it flips to `done` |
|---|---|---|---|
| U1 | `feat/idn-store` | none | FR-IDN-01, 02, 05, 06, 07, 08; FR-KEY-01..08; FR-CRD-06, 07, 09; FR-AUD-01..06; FR-OPS-05, 06, 08; (part of FR-CFG-04: `GATEWAY_ISSUE_KEYS`, `UPSTREAM_API_KEY`, `AUDIT_RETENTION_DAYS`) |
| U2 | `feat/idn-auth` | U1 | FR-IDN-03, 04; FR-ATH-01..12; FR-IDP-01..07; FR-OPS-07; rest of FR-CFG-04 except portal and `KB_TRUST_SECRET` |
| U3 | `feat/idn-authz` | U1 (and U2 for `TurnRequest.principal`) | FR-ENT-01..10 |
| U4 | `feat/idn-portal` | U1, U2 | FR-PRT-01..12; FR-CRD-08; portal part of FR-CFG-04 |
| U5 | `feat/idn-upstream-key` | U1 | FR-UPS-01..07 |
| U6 | `feat/kb-groups` | none | FR-VIS-01..12 and the amended kb rows |
| U7 | `feat/gw-kb-identity` | U1, U2 (principal), U6 for the e2e only | FR-KBI-01..04; `KB_TRUST_SECRET` part of FR-CFG-04 |
| U8 | `feat/oidc-device-link` (in the `telegram-bot-gateway` repository, via a worktree of the submodule) | none | the bot rows in `contracts/bot-requirements.md` |
| U9 | `feat/idn-stack` | none to start; needs everything to run the e2e | none (stack wiring and docs) |

---

## U1: gateway identity foundation (`feat/idn-store`)

- [ ] T101 `agent-gateway/src/agent_gateway/domain/identity.py`: `KeyScope`, `Principal`,
      `DirectoryEntry`, `KeyInfo`, `AuditEvent`, the error types, `valid_group`, `clean_groups`,
      `clean_roles`, `is_active`; `Principal` and `DirectoryEntry` carry `roles`
      (contracts/ports.md sections 1 and 2).
- [ ] T102 `application/identity_ports.py`: the provider bundle (`Identity`, `Account`,
      `TokenVerifier`, `DirectorySource`, `PortalLogin`) and `DirectoryStore`, `KeyService`,
      `CredentialStore` additions, `AuditLog`, `OwnerPurge` (contracts/ports.md section 3). The
      bundle is defined here so U2 and U4 build against it; no provider name appears in this file.
- [ ] T103 `application/key_service.py`: the `KeyService` use cases over a store port (hash,
      prefix rules, replace-on-register, flag check, `last_used_at` throttle), pure and fake-tested.
      `application/audit.py`: folding of repeats within 60 s, `detail` truncation, secret-free guard
      (rejects a `detail` whose serialisation contains a known sentinel in tests).
- [ ] T104 `agent-gateway/migrations/005_identity.sql` exactly as in data-model.md
      (`directory_snapshot` has `groups` and `roles`).
- [ ] T105 `adapters/outbound/postgres_store.py`: keys (issue, register, resolve, list, revoke,
      upstream key), snapshot read/replace, audit record/recent/prune, credentials `list_vars` and
      `delete_var`, owner purge. Retire `find_user`/`create_key`/the old `list_keys` and their users.
- [ ] T106 `tests/application/fakes.py`: in-memory fakes of every new port (including a fake
      provider bundle), used by U2 to U7.
- [ ] T107 CLI (`adapters/inbound/cli.py`): `key register-llm --owner`, `key create` (refuses without
      the flag), `key list --owner`, `key revoke`, `creds list/set/delete [--var]`, `audit list
      [--limit --actor --action --since]`, `owner purge`. Remove the old `user`/free-text key
      commands. `Makefile` targets in the gateway module renamed accordingly (U9 adjusts root targets).
- [ ] T108 Settings: `GATEWAY_ISSUE_KEYS`, `UPSTREAM_API_KEY`, `AUDIT_RETENTION_DAYS` parsed once in
      the settings module; `.env.agent-gateway.example` updated.
- [ ] T109 Tests (all `# Covers:`): hash and prefix rules; replace-on-register; duplicate refused
      without owner leak; flag off refuses issue; revoked secret wiped; listing has no secret; audit
      folding; sentinel scan; Postgres tests in `tests/integration/` (migration applies on a database
      holding the 001 to 004 schema and leaves the right shape).
- [ ] T110 Spec: flip rows; update `agent-gateway/README.md`/AGENTS.md command list.

## U2: authentication (`feat/idn-auth`)

- [ ] T201 `application/authenticate.py` (`Authenticator`) and `application/directory_sync.py`
      (`DirectorySync`: `fetch_all`, `replace_all`, failure-streak audit, on-demand refresh with the
      30 s floor), both provider-neutral, over the U1 ports and fakes.
- [ ] T202 `adapters/outbound/identity/keycloak/verifier.py`: `TokenVerifier` with `pyjwt`,
      algorithm allowlist (RS256, ES256), `iss`, `aud`, `exp`/`nbf` with 60 s leeway, 8 KiB limit,
      JWKS cache (TTL, refetch on unknown `kid` at most once per 30 s, stale-if-error); reads groups
      from the token (`groups` claim; roles only from `resource_access.<OIDC_AUDIENCE client>.roles`,
      realm roles ignored). Dependency added to `pyproject.toml`.
- [ ] T203 `adapters/outbound/identity/keycloak/directory.py`: `DirectorySource.fetch_all`: service
      account token, paged users, group members (flatten subgroups to leaf names), the effective client-role mappings of the
      gateway client per user (composite endpoint, includes group-inherited roles; client uuid looked
      up once), all-or-nothing, read requests only. `.../settings.py`: the `OIDC_*` and
      `DIRECTORY_*` settings and their validation (read only here). `.../portal_login.py`:
      `PortalLogin` (authorize URL, code exchange with PKCE, ID token check). `.../__init__.py`:
      the factory the composition root maps `IDENTITY_PROVIDER=keycloak` to; an unknown value stops
      startup naming the allowed values.
- [ ] T203b `tests/providers/contract.py` (the reusable `IdentityProviderContract`, per
      contracts/provider-contract-tests.md) and `tests/providers/test_keycloak_contract.py` running it
      against the Keycloak adapter with recorded or faked HTTP and a test key pair. Boundary test: no
      import of `adapters/outbound/identity/keycloak` outside the composition root and the tests, and
      no `keycloak`/`OIDC_` string in `domain/` or `application/`.
- [ ] T204 `adapters/inbound/openai_http/auth.py`: resolve the principal once per request, map
      `AuthError` to 401/503 in the contract's error shape, rate-limit per person; `/healthz` and the
      portal sign-in routes stay open. `TurnRequest.principal`; `user_id` stays as a property.
- [ ] T205 Startup: validate settings (headers-and-env.md rules); first sync before serving (30 s
      bound); background task; pruning task for audit.
- [ ] T206 CLI: `directory sync`, `directory show`, `directory orphans`.
- [ ] T207 Tests: (provider cases are the contract suite) key-first order; stale snapshot
      503; disabled person with a valid token; unknown person triggers one refresh; JWKS rotation;
      outage behaviour with a fake clock; service principal kind.
- [ ] T208 Spec rows, README.

## U3: authorisation (`feat/idn-authz`)

- [ ] T301 `domain/access.py` and `AgentAccess(groups, roles, write_groups, write_roles)` in
      `domain/agent.py` (validation per yaml-schemas.md; any-of matching over groups and roles).
- [ ] T302 `adapters/inbound/openai_http/models.py` filtering; `domain/routing.py` refusal as
      `model_not_found`.
- [ ] T303 `application/turn.py`: handoff offers, thread continuation re-check, pending resume
      re-check, approval-gated tool hiding in `build_toolset` using `can_write` and the key scope.
- [ ] T304 `check` command: warnings for write-only groups and unknown group names.
- [ ] T305 Example agents with `access` blocks in `agent-gateway/config/agents/` and the agent-file
      contract `agent-file.schema.yaml`.
- [ ] T306 Tests per spec.md story 4 scenarios 1 to 8.

## U4: portal (`feat/idn-portal`)

- [ ] T401 `adapters/inbound/portal/`: `session.py` (cookies per data-model section 8), `csrf.py`,
      `routes.py` (contracts/http-portal.md), `templates/`, `static/portal.css`; mount in
      `create_app` only when `PORTAL_BASE_URL` is set. Dependencies `jinja2`, `python-multipart`.
- [ ] T402 Sign-in through the `PortalLogin` port only (`authorize_url`, `complete`), with the fake
      provider bundle in tests; the portal imports nothing provider-specific and never reads
      `OIDC_*`. The Keycloak implementation of `PortalLogin` is U2's.
- [ ] T403 `mcp-servers.yaml` credential declarations (string or object), `test` block, loader and
      model changes; the store refuses undeclared pairs.
- [ ] T404 Audit writes in the same transaction as each change.
- [ ] T405 Tests: sentinel never in a page; CSRF and origin refusals; disabled person signed out;
      issued-keys card gated by the flag; secret shown once; static headers.
- [ ] T406 Docs: `agent-gateway/docs` portal section.

## U5: per-person model key (`feat/idn-upstream-key`)

- [x] T501 `UpstreamCredentials` port and implementation over `KeyService` and the settings.
- [x] T502 `adapters/outbound/upstream_llm.py`: key per call; `application/turn.py` and the
      compaction call pass the principal's key; `openai_http/proxy.py` substitutes `Authorization`
      and drops the caller's; `models.py` listing uses it.
- [x] T503 Error mapping: `NoUpstreamKey` to 403 `upstream_key_required` (portal address from
      `PORTAL_BASE_URL`), provider 401/403 to 502 `upstream_key_rejected` with audit.
- [x] T504 Tests with a stub upstream that records the header: agent turn, compaction, pass-through,
      listing, resume by the resuming caller; sentinel scan of logs.

## U6: knowledge base groups (`feat/kb-groups`)

- [ ] T601 `kb/docs/SPEC.md`: flip FR-VIS rows and apply the amended wording of N3, FR-ENT-10,
      FR-MCP-04, NFR-CFG-03, C4, C6, section 11, Q1 (already staged as planned changes).
- [ ] T602 `sources_config.py`: `groups` field and validation; rejected under `defaults`.
- [ ] T603 `migrations/005_source_visibility.sql` as in data-model.md; store port and Postgres
      adapter; scheduler upsert before a source's entries are written; retention of the row.
- [ ] T604 `Caller`, filters in `SearchCatalog`, `GetEntry`, and in the store queries (predicate
      applied before ranking and limits); stored content follows the entry.
- [ ] T605 `mcp_server.py`: trust-header rule, constant-time compare, startup log line; `config.py`
      reads `KB_TRUST_SECRET`; `kb/.env.example`; `kb/compose.yaml` adds the variable to `mcp-kb`'s
      explicit list; `test_deployment.py` pins the list.
- [ ] T606 Operator guide and `CONFIGURATION.md`; admin console unaffected (verify it still reads
      all sources).
- [ ] T607 Tests: spec.md story 6 scenarios 1 to 7; trust-header table; fail-closed on a missing row;
      Postgres tests under `make kb-test`.

## U7: gateway to kb forwarding (`feat/gw-kb-identity`)

- [ ] T701 `ToolGateway.call(..., identity=)`; `McpTools._headers` adds `X-KB-Groups` and
      `X-KB-Trust` for `forward_identity` servers only; sanitising and limits.
- [ ] T702 `KbRetriever` forwards the same; `mcp-servers.yaml` flag set on `kb`; startup warning when
      the flag is set and `KB_TRUST_SECRET` is empty.
- [ ] T703 Tests: only flagged servers get the headers; secret never in model context or logs;
      commas and control characters dropped; no principal, no headers.

## U8: Telegram device link (`feat/oidc-device-link`, bot repository)

- [ ] T801 Apply `contracts/bot-requirements.md` to the bot's `docs/spec.md` (rows `planned`),
      including the section 6 row change. Generic wording only; nothing about the stack.
- [ ] T802 `application/linking.py`, new ports and fakes, `adapters/outbound/oidc.py`, migration
      `002_oidc_links.sql` as in data-model.md, config, `/login` and `/logout`, CLI listing.
- [ ] T803 Tests with the fake `IdentityProvider` for every row; flip rows to `done`.
- [ ] T804 `docs/OPERATOR_GUIDE.md` section; `.env.example`.
- [ ] T805 Bump the submodule pointer in the stack as a separate one-line commit by the coordinator.

## U9: Keycloak and stack wiring (`feat/idn-stack`)

- [ ] T901 `integrations/keycloak/realm/agentflow-realm.json` per contracts/keycloak-realm.md;
      `integrations/keycloak/apply.sh` (idempotent, creates secrets, test people for the e2e only).
- [ ] T902 `.env.agent-gateway.example` and `kb/.env.example` entries; `prepare` scripts generate and
      share `KB_TRUST_SECRET`; root `docker-compose.yml`/`Makefile` wiring (gateway portal URL,
      `make keycloak-apply`, renamed gateway user targets); `integrations/telegram-bot/` env for
      device mode.
- [ ] T903 `scripts/identity-e2e.sh` and `scripts/stub-model.py` implementing quickstart steps 2 to 9.
- [ ] T904 Docs: `docs/KEYCLOAK.md` (realm, runbook, register-existing-users-fresh),
      `docs/CONFIGURATION.md`, `docs/OPERATIONS.md` (backup: `CREDS_KEY` with the data), `CLAUDE.md`
      Architecture and Day to day, `docs/ARCHITECTURE.md`.
- [ ] T905 Update the project memory note `user-management-direction.md` (coordinator).

## After the units

- [ ] T990 Coordinator merges in order, runs [`quickstart.md`](quickstart.md), records R1's outcome
      in `research.md`, and flips any remaining rows.
- [ ] T991 `/speckit-analyze` over this directory against the merged code.
