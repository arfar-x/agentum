<!--
Sync Impact Report
Version: 1.2.0 -> 1.3.0 (MINOR)
Modified principles: I (application modules), II (spec of record), III (hexagonal core), IV (published ports),
  Additional Constraints (no new infrastructure), Development Workflow (gates and commit scopes)
Added: telegram-bot-gateway/ named as an application module, with the rules that follow from it
Removed: none
Templates reviewed: .specify/templates/plan-template.md (constitution check reads this file; no change)
Follow-up: none
-->

# agentflow Constitution

## Core Principles

### I. Configuration over code

agentflow is an infrastructure repository: a pinned `docker-compose.yml`, configuration templates
and scripts around LibreChat, a self-hosted OpenAI-compatible model endpoint and MCP servers.

- A need that LibreChat or another upstream component could meet MUST be met by configuration
  here, or built generically upstream (a LibreChat contribution, an `agent-skills` release). It
  MUST NOT be met by patching an upstream component inside this repository.
- `kb/`, `agent-gateway/` and `telegram-bot-gateway/` are the application modules of this
  repository's own. `agent-gateway/` and `telegram-bot-gateway/` are generic and self-contained so
  they can become their own repositories: they name no model or organization, and agentflow only
  wires them up. `telegram-bot-gateway/` MUST NOT import another module's code; it depends only on
  an OpenAI-compatible Chat Completions endpoint.
- Nothing tracked MAY name a specific model, organization or person. Such details live in `.env`
  and the generated configuration.
- Generated files (`config/librechat.yaml`, `searxng/settings.yml`) MUST NOT be edited by hand.
  Edit their tracked templates.

Rationale: every line of forked behavior is a line that breaks on the next upstream upgrade.

### II. The spec of record leads

`docs/spec/` (and `agent-gateway/docs/spec.md` and `telegram-bot-gateway/docs/spec.md` for the
gateway and the Telegram bot, which travel with their modules)
holds the specification of record for anything large enough to design before building.

- A behavior change MUST land in the spec in the same change as the code, never after it.
- Every requirement MUST carry a stable id and a status.
- A requirement marked `done` MUST have a test that claims it (`# Covers: <id>`).
  `kb/tests/test_spec_coverage.py`, `agent-gateway/tests/test_spec_coverage.py` and
  `telegram-bot-gateway/tests/test_spec_coverage.py` enforce this pairing and MUST stay green.
- Spec Kit feature directories (`specs/NNN-*/`) are working documents for one change. Whatever
  they decide about `kb/` MUST also be written into `kb/docs/SPEC.md`, and about
  `agent-gateway/` into `agent-gateway/docs/spec.md`, before the change is complete.
  `agent-gateway/` and `telegram-bot-gateway/` keep their own Spec Kit directories under
  `agent-gateway/specs/` and `telegram-bot-gateway/specs/`, so they travel with the module.
  The registry of ids stays in the module's `docs/spec.md`; each module's `specs/` maps its
  requirements to those ids.

Rationale: the code follows the spec. When the two disagree, one of them is wrong, and only a
written spec makes that visible in review.

### III. Hexagonal, provable core (NON-NEGOTIABLE for `kb/`, `agent-gateway/` and `telegram-bot-gateway/`)

- `kb/`, `agent-gateway/` and `telegram-bot-gateway/` keep `domain/` → `application/` → `adapters/`.
- `domain/` and `application/` MUST NOT import anything that does I/O (a database driver, HTTP
  client, MCP framework, web framework, agent-runtime library, YAML parser or standard-library
  I/O module). Each module's `tests/test_boundaries.py` enforces this.
- Use cases MUST be provable with in-memory fakes, without a database, network or model.
- Data crossing a boundary MUST be validated by a typed model at the crossing.
- New behavior arrives with tests:
  - unit tests run with no services (`cd kb && python -m pytest`, `cd agent-gateway && python -m pytest`,
    `cd telegram-bot-gateway && python -m pytest`);
  - anything touching Postgres is also covered by `make kb-test`.

Rationale: the catalog's rules have to be checkable in under a second, on a laptop, with no
infrastructure.

### IV. Secrets stay in the environment; internal services stay internal

- Every secret lives in `.env` or `searxng/settings.yml`, both gitignored. Tracked files hold
  `${VAR}` references and placeholder values only.
- `api` and `agent-gateway` are the only services end users or external clients reach. `agent-gateway`
  MUST authenticate every request, scope all state to the authenticated user and rate-limit. The only
  other kind of service that MAY publish a port is an operator tool that has its own credential:
  `admin-panel`, and the opt-in `kb-webhook` and `kb-admin`, which also bind loopback by default and
  are off unless their compose profile is enabled. A new one needs the same, stated in its plan's
  constitution check.
- `telegram-bot-gateway` publishes no port: it polls Telegram. It joins only its own network, shared
  with `agent-gateway`, and `gateway-data` for its own database; it MUST NOT join `backend`. Each
  linked Telegram user acts under their own gateway key, and the module has no allowlist.
- MCP servers, `searxng`, `crw`, `kb-db` and every other internal service MUST have no `ports:` entry.
  Network reachability from `api` (or, for the gateway's own tool containers, from `agent-gateway`
  alone) is their only access control, and `agent-gateway` MUST NOT join `backend`.
- `CREDS_KEY`, `CREDS_IV` and the JWT secrets are coupled to the data they encrypt. Changes to
  backup or restore MUST keep them together.

Rationale: MCP's HTTP transport has no authentication of its own, and a leaked `.env` value is
the most likely way this stack gets compromised.

### V. Least privilege per component

Each component holds only what its job needs.

- `mcp-kb` serves reads with a read-only database role and MUST hold no source-system
  credentials. Work that needs credentials runs in `kb-scheduler`, or in the per-user tool that
  owns them.
- Source systems enforce their own permissions when a user reads through their own credentials.
- Anything the catalog itself serves is shared with every catalog user. It MUST only include
  what an operator opted into, per source, in tracked configuration.
- Writes to external systems go through both approval layers: LibreChat `toolApproval` (or, for
  a tool the gateway runs itself, the gateway's approval gate), and the toolset's own
  `--confirm`. Neither layer is removed because the other exists. A write the user's own client
  executes is gated by that client's permission prompt.

Rationale: a component that can't reach a credential can't leak it, and a sharing decision
written in tracked configuration is reviewable.

## Additional Constraints

- **No new infrastructure by default.** The stack runs on one host with one self-hosted
  OpenAI-compatible model endpoint, and no embeddings model or dedicated retrieval service.
  A change that needs more MUST say so in its plan and justify it. Lightweight stores
  (Postgres, Redis) are acceptable when justified; heavy ones (search clusters, brokers) are not.
  `agent-gateway` owns its own Postgres and Redis instances; `telegram-bot-gateway` keeps its state in
  its own database and role on the gateway's Postgres instance and adds no instance of its own.
- **Bilingual.** User-facing knowledge is in Persian and English. Language-keyed data stays
  language-keyed, not fixed to two languages.
- **Pinned versions.**
  - Images are pinned by tag in `.env`.
  - `agent-skills` is a submodule pinned to a released tag.
  - Upgrades are reviewable, one-purpose commits with the runbook in `docs/OPERATIONS.md`.
- **Declarative state.** LibreChat agents (`agents/*.yaml`), gateway agents
  (`agent-gateway/config/`), knowledge base sources
  (`kb/config/sources.yaml`) and configuration templates are the source of truth for what they
  list. Anything derived (the catalog index, generated configs) MUST be rebuildable from them.

## Development Workflow

- **Branches.** Work happens on a branch cut from the current `main`, never on `main` itself.
- **Commits.** Each commit is one logical step and is described by its message: Conventional
  Commits, with the scope naming the area (`kb`, `gateway`, `telegram`, `librechat`, `spec-kit`, `readme`).
- **Spec Kit order.** For a change big enough to design:
  1. `/speckit-specify`
  2. `/speckit-clarify` (when anything is ambiguous)
  3. `/speckit-plan`
  4. `/speckit-tasks`
  5. `/speckit-analyze`
  6. `/speckit-implement`

  Each artifact is committed on its own.
- **Gates before a PR:**
  - `cd kb && python -m pytest` (spec coverage and boundaries included) for any `kb/` change, `cd agent-gateway && python -m pytest` for any `agent-gateway/` change, and `cd telegram-bot-gateway && python -m pytest` for any `telegram-bot-gateway/` change;
  - `make kb-test` when storage changes, `make gateway-test` for gateway integration, `make telegram-test` for the Telegram bot;
  - config changes rendered and validated against the pinned LibreChat image before they ship.
- **Docs.** Operator-visible behavior is documented in the same change:
  - `docs/CONFIGURATION.md` for variables;
  - `kb/docs/OPERATOR_GUIDE.md` for the catalog;
  - `docs/OPERATIONS.md` for runbooks.
- **Merging.** A pull request is opened, not merged, by whoever wrote it. Merging is a human
  review decision.

## Governance

- This constitution governs how changes to agentflow are designed and reviewed.
- `AGENTS.md` (with `CLAUDE.md` as a symlink to it) remains the runtime guide for agents working
  in the repository. Where the two disagree, this document wins until one of them is amended.
- An amendment is a reviewed pull request that edits this file and states its version bump:
  - MAJOR when a principle is removed or redefined;
  - MINOR when a principle or section is added or materially expanded;
  - PATCH for wording.
- Plans produced by `/speckit-plan` MUST include a constitution check. A violation is either
  removed from the plan or recorded with its justification.

**Version**: 1.3.0 | **Ratified**: 2026-10-04 | **Last Amended**: 2026-10-21
