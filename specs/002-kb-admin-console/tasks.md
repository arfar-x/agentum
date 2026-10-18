# Tasks: Knowledge base admin console

All tasks are complete; the requirement ids are in `docs/spec/knowledge-base.md`.

## Phase 1: Spec and deployment

- [x] Spec v2.8.0: §6.11, `FR-SCH-08..13`, `FR-CFG-10..12`, `FR-CLI-06`, `FR-SRC-07`, `NFR-DEP-08`
- [x] Migration `004_admin_console.sql`, `grants/admin.sql`, `kb_admin` in `scripts/kb-init.sh`
- [x] `kb-admin` compose service (profile `admin`), `.env.example`, `generate-secrets.sh`, Makefile
- [x] Deployment assertions in `tests/test_deployment.py`

## Phase 2: Scheduler and run history

- [x] `RunLog`, `SyncQueue`, `HeartbeatStore` ports, fakes and Postgres adapters
- [x] `RunRecorder`; the scheduler records runs at both ends, recovers interrupted runs, seeds cadence
- [x] `next_due` and its consistency test against `due`
- [x] Scheduler drains `sync_request`s (sync, purge, retire) and beats from its own thread
- [x] CLI `sync` records runs

## Phase 3: Admin service

- [x] Auth (token, signed cookie, CSRF, lockout), read models, runs, schedules, issues, check
- [x] Request endpoints (sync, cancel, purge, retire)

## Phase 4: Source editing

- [x] `RuamelSourceEditor`, `FileSourcesFile`, `EditSources` (preview, apply, etag, revisions)
- [x] `PostgresSourceHistory`: mirror and append-only revisions

## Phase 5: Manual records

- [x] `form` location kind, `FormRecord`, `ManageForms`, `DraftSummary`
- [x] `PostgresFormRecords`, `FormKnowledgeSource`, `PresetSummarizer`
- [x] Scheduler publishes `form` through the same funnel, with or without a readable sources file
- [x] `/api/forms*` endpoints, record editor, content inspector

## Phase 6: Front end and docs

- [x] SPA: overview, sources, schedules, reports, issues, form records
- [x] CI job (typecheck, test, build)
- [x] Operator documentation: `docs/KNOWLEDGE_BASE.md`, `docs/CONFIGURATION.md`, `docs/OPERATIONS.md`,
      `README.md`, `AGENTS.md`, `kb/README.md`, `kb/AGENTS.md`

## Phase 7: Production hardening

- [x] A lost database connection is replaced, not fatal (`NFR-DEP-09`); proved by restarting Postgres under the end-to-end run
- [x] Health check reflects the database (`FR-ADM-17`)
- [x] Request bodies bounded; a record's text fits what the catalog keeps (`FR-ADM-18`)
- [x] Login lockout is per visitor behind a trusted reverse proxy, and bounded (`KB_ADMIN_FORWARDED_ALLOW_IPS`)
- [x] A configuration write is verified and undone if it fails (`FR-CFG-13`)
- [x] `starlette` and `uvicorn` declared by the `admin` extra; Node base image pinned to a minor
- [x] Image steps simulated without Docker (clean `npm ci && npm run build`, clean `pip install`, console served from them)
- [x] `docker compose config` validated in CI with every optional profile on
- [x] Upgrade from a database holding 001-003 and run history (`tests/integration/test_upgrade.py`)
- [x] Constitution 1.1.0: operator tools may publish a port when opt-in, loopback by default and credentialed
- [x] Independent security and correctness review; `npm audit` and `pip-audit` clean
