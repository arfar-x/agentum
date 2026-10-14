---

description: "Task list for the agent gateway"
---

# Tasks: Agent Gateway

**Input**: design documents in `agent-gateway/specs/001-agent-gateway/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/
**Tests**: required (constitution III): every `done` requirement in `agent-gateway/docs/spec.md` has a test with `# Covers: <id>`.

Paths are relative to `agent-gateway/` unless they start with `/` (repo root). `[P]` = parallelizable, `[USn]` = spec user story.

## Phase 1: Setup

- [x] T001 Create `pyproject.toml` (Python >=3.12, deps and extras per plan.md, pytest `pythonpath=["src"]`, asyncio auto mode) and the `src/agent_gateway/{domain,application/use_cases,adapters/inbound/openai_http,adapters/outbound}` package skeleton
- [x] T002 [P] Write `docs/spec.md`: the spec of record, SPEC-GW-001, FR/NFR/AS tables in the format of `/docs/spec/knowledge-base.md`, all `planned`
- [x] T003 [P] Write `tests/test_boundaries.py` (domain and application MUST NOT import httpx, fastapi, starlette, uvicorn, langgraph, langchain*, fastmcp, mcp, yaml, psycopg, redis, requests, cryptography, `agent_gateway.adapters`; nor stdlib socket, urllib, sqlite3, http, subprocess, asyncio) modelled on `kb/tests/test_boundaries.py`
- [x] T004 [P] Write `tests/test_spec_coverage.py` modelled on `kb/tests/test_spec_coverage.py`, reading `docs/spec.md`
- [x] T005 [P] Write `Dockerfile` (python:3.12-slim, non-root, uvicorn entry), `Makefile` (test, lint, run), `README.md`, `AGENTS.md`

## Phase 2: Foundational (blocks every story)

- [x] T010 [P] `domain/messages.py`: typed OpenAI chat message, tool call, tool definition and usage models (pydantic, `extra="forbid"` except on passthrough fields)
- [x] T011 [P] `domain/agent.py`: `AgentDefinition` per data-model.md (id pattern `^[a-z0-9][a-z0-9-]*$`, `client_system` in append|prepend|ignore, `client_tools` all|none|list, `activity` narrate|quiet; unknown fields rejected; handoff targets validated in a `validate_agent_set` function)
- [x] T012 [P] `domain/routing.py`: `parse_route(model, agents)` → agent route (optional `agent:upstream-model` override honoured only when `allow_model_override`) or passthrough
- [x] T013 [P] `domain/params.py`: `merge_parameters(defaults, client, allow_overrides, locked)`; rejects `n>1` and `logprobs` for agents with an error naming the parameter
- [x] T014 [P] `application/ports.py`: ports per contracts/internal-ports.md
- [x] T015 [P] `tests/application/fakes.py`: in-memory fakes for every port, plus a scripted fake `Llm`
- [x] T016 `adapters/outbound/config_files.py`: load and validate `config/agents/*.yaml`, `config/mcp-servers.yaml`, `config/gateway.yaml` (env interpolation; startup fails naming file and problem — FR-027); ship minimal example files
- [x] T017 `adapters/inbound/openai_http/errors.py`: OpenAI error objects, status mapping, exception handlers
- [x] T018 `adapters/outbound/postgres_store.py` + `migrations/001_init.sql`: tables `api_keys`, `user_credentials`, `threads`, `pending_turns` exactly per data-model.md; implements `KeyStore`, `CredentialStore`, `ConversationStore` with atomic `take_pending`
- [x] T019 `adapters/outbound/crypto.py`: Fernet encrypt/decrypt for credentials; plaintext never logged
- [x] T020 `adapters/inbound/openai_http/auth.py`: bearer auth dependency resolving a key to a user; Redis-backed per-key rate limit (`adapters/outbound/redis_locks.py`: `RateLimiter`, `Lock`)
- [x] T021 `container.py` and `config.py`: wiring from env (`Settings.from_env`), nothing org- or model-specific
- [x] T022 `adapters/inbound/cli.py`: `key create|list|revoke`, `creds set|delete`, `check` (config validation)
- [x] T023 Tests: domain tests for T010–T013; adapter tests for T016–T020 (Postgres/Redis via a throwaway-container script `scripts/test-services.sh`)

## Phase 3: US2 — Direct model use and passthrough (P1)

**Goal**: any non-agent request behaves as if the upstream were called directly.
**Independent test**: same request direct and through the gateway returns equivalent bodies; embeddings route works.

- [x] T030 [P] [US2] `adapters/outbound/upstream_llm.py`: httpx client (`Llm` port) with retry/backoff on transient errors (FR-021)
- [x] T031 [US2] `adapters/inbound/openai_http/proxy.py`: streaming reverse proxy for `/v1/*`, swap key, pass bytes untouched
- [x] T032 [US2] `adapters/inbound/openai_http/app.py`: FastAPI app, routes registered, `/healthz`
- [x] T033 [US2] Tests: passthrough fidelity against a stub upstream, error shape on upstream failure (`tests/adapters/test_proxy.py`)

## Phase 4: US1 — Use an agent as a model (P1) 🎯 MVP

**Goal**: an unmodified OpenAI SDK chats with an agent, streamed and not.
**Independent test**: SDK conformance run against a stub upstream; models listing; parameter override and lock.

- [x] T040 [P] [US1] `application/use_cases/list_models.py` and `adapters/inbound/openai_http/models.py`: upstream models + agents; agents still listed when upstream fails
- [x] T041 [P] [US1] `domain/prompt.py`: compose system prompt from instructions, activity block and client system prompt per `client_system`
- [x] T042 [US1] `adapters/outbound/langgraph_runtime/graph.py` and `nodes.py`: StateGraph with `model` node and loop edge; wired to the `Llm` port; emits events
- [x] T043 [US1] `application/use_cases/run_turn.py`: authenticate-scoped turn orchestration, lock, thread lookup/seed, hand to `AgentRuntime`, aggregate usage (FR-005, FR-026)
- [x] T044 [US1] `adapters/inbound/openai_http/chat.py`: chat completions for agents, streaming and non-streaming, `stream_options.include_usage`, response shape per contracts/openai-http.md
- [x] T045 [US1] `tests/conformance/`: OpenAI Python SDK against agent and passthrough models, recorded-shape assertions (SC-001, SC-002)
- [x] T046 [US1] Tests for key refusal, parameter override/lock, unsupported `n`/`logprobs` (spec US1 scenarios 4–5)

## Phase 5: US3 — Forced knowledge retrieval (P1)

**Goal**: catalog hits are in context before the model's first step.
**Independent test**: scripted model whose answer requires a hit; no model search call; catalog failure still answers.

- [x] T050 [P] [US3] `adapters/outbound/mcp_tools.py` (part 1): `Retriever` over the kb MCP server
- [x] T051 [US3] `domain/retrieval.py`: format hits into a context message; failure note
- [x] T052 [US3] `retrieve` node in `langgraph_runtime/nodes.py`, per-agent `preretrieval.kb` settings, optional LLM query rewrite
- [x] T053 [US3] Tests incl. unreachable catalog and retrieval off (`tests/application`, `tests/adapters`)

## Phase 6: US7 — Self-correcting loop (P2)

**Goal**: model mistakes never fail the run.
**Independent test**: scripted bad-then-good model sequences.

- [x] T060 [P] [US7] `domain/corrections.py`: error kinds (unknown tool, invalid JSON, schema violation, empty reply) → concise hint text, with no secrets
- [x] T061 [US7] Wire corrections and a bounded attempt limit (gateway.yaml) into the graph; exceeding the limit raises a standard error
- [x] T062 [US7] Tests for each error kind and for limit exhaustion, transient-retry path

## Phase 7: US4 — Tool routing and credentials (P2)

**Goal**: choose client / request-credential / stored-credential / delegate per call.
**Independent test**: the four-way scenario in quickstart step 6.

- [x] T070 [P] [US4] `domain/toolroute.py`: name normalization (`mcp__<server>__x`, `<server>_x`, `x_mcp_<server>`), alias table, `choose_route(call, client_tools, request_creds, stored_creds)`
- [x] T071 [P] [US4] `application/use_cases/credentials.py`: resolve per server/var (header > stored), user-scoped
- [x] T072 [US4] `mcp_tools.py` (part 2): `ToolGateway` using per-call credential headers (prefix from `mcp-servers.yaml`), tool schema cache per server, results-as-errors
- [x] T073 [US4] Graph `tools` node: partition calls, run gateway ones, return client ones as `tool_calls`, persist `PendingTurn(kind=client_tool)` and splice hidden messages on resume
- [x] T074 [US4] Tests: four routing cases, cross-user credential isolation, no secret in logs

## Phase 8: US5 — Approval in conversation (P2)

**Goal**: gateway-run writes are previewed and approved.
**Independent test**: reject then approve; tampered/reused/expired/foreign code refusals.

- [x] T080 [P] [US5] `domain/approval.py`: HMAC code bound to (user, call, tool, args hash, expiry); verify; single-use semantics
- [x] T081 [P] [US5] `domain/interaction.py`: ask-tool registry (name, argument template, answer parser) loaded from `gateway.yaml`
- [x] T082 [US5] Approval node: preview via client ask tool when declared, else text with code; strip model-supplied `confirm`; add it only on the approved exact call
- [x] T083 [US5] `application/use_cases/resume.py`: resume on tool result or `approve|reject <code>`; atomic consume
- [x] T084 [US5] Tests incl. client-run writes untouched (FR-017)

## Phase 9: US6 — Handoffs and visible activity (P2)

**Goal**: specialists take over; activity visible without client detection.
**Independent test**: router → specialist, follow-up stays; activity as `reasoning_content` and narrated line.

- [x] T090 [P] [US6] `domain/activity.py`: events and the narration instruction block text
- [x] T091 [US6] Generated `transfer_to_<id>` tools, `active_agent` in thread record, graph routing between agent nodes
- [x] T092 [US6] Stream/non-stream `reasoning_content` emission and keepalives for long tool calls
- [x] T093 [US6] Tests for handoff continuity, hash-chain miss fallback, concurrent requests serialized (Redis lock)

## Phase 10: Responses API (P2, serves US1)

- [x] T100 `adapters/inbound/openai_http/responses.py`: `/v1/responses` for agents (`input`, `instructions`, `previous_response_id`, tools, streaming events); passthrough otherwise
- [x] T101 Response→thread mapping in `ConversationStore`; tests with the SDK's responses client

## Phase 11: US8 — Operations (P3)

- [x] T110 [US8] Two-user isolation integration test (conversations, approvals, credentials); revocation within 5 s (SC-008)
- [x] T111 [US8] Rate-limit and request-size tests; startup config-error test (FR-027)

## Phase 12: agentflow wiring and docs

- [x] T120 Port the six agents to `config/agents/*.yaml` and write `config/mcp-servers.yaml`, `config/gateway.yaml`
- [x] T121 `/docker-compose.yml`: services `agent-gateway`, `gateway-db`, `gateway-redis`, `mcp-agent-skills-gateway`; networks `gateway-tools`, `gateway-kb`; volumes; one published port
- [x] T122 `/.env.agent-gateway.example`; `/Makefile` targets `gateway-up`, `gateway-test`, `gateway-key-*`, `gateway-creds-set`; backup coverage
- [x] T123 Docs: `README.md` client recipes (SDK, coding agents, LibreChat), credential-header guidance; `/AGENTS.md`/`/CLAUDE.md` architecture and trust chain; `/docs/OPERATIONS.md`
- [x] T124 Mark each implemented requirement `done` in `docs/spec.md` in the same commit as the test claiming it; run quickstart

## Dependencies

Setup → Foundational → US2 → US1 → (US3, US7 parallel) → US4 → US5 → US6 → Responses → US8 → wiring. US2 first because US1 reuses its client and app. US5 needs US4's pending-turn mechanism. US6's handoff needs the thread record from US1.

## Parallel examples

- T002–T005 together; T010–T015 together.
- US3 (T050–T053) and US7 (T060–T062) touch different files once US1 lands.

## Strategy

MVP = Phases 1–5 (passthrough, agent chat, retrieval). Then tool routing and approval, handoffs, Responses, operations, wiring. Each phase ends with its spec rows flipped to `done` and tests green.

## Amendments from analysis (2026-10-14)

- T018/T019: `pending_turns.hidden_messages` is Fernet-encrypted and rows carry a short TTL (S1).
- T016: `gateway.yaml` carries defaults for approval expiry, thread retention, rate limits, request size, loop-attempt limit (A1).
- T002: `docs/spec.md` includes a traceability table from this spec's FR/SC ids to its own (I2).
- T070a: spike confirming per-call credential headers reach the tool server before T072 (S2).
- T110 adds a restart-survival test (FR-025); T033 adds a passthrough latency check (SC-003); T111 names the request-size test (FR-024).

## Status (2026-10-14)

All phases are implemented and every requirement in `docs/spec.md` is `done` with a claiming test.
Open items that tests here cannot settle:

- **SC-003** (passthrough overhead under 50 ms median) is not measured; it needs a real upstream.
- **Ask-tool argument shapes** (`AskUserQuestion`, `ask_user_question`, `question`, `clarify`) are
  best-effort until checked against each client; the signed-code fallback works regardless.
- **Client tool naming** for the user's-client-first routing is normalized by prefix/suffix rules
  and `config/gateway.yaml` aliases; confirm against the clients in use.
- **SC-010** (four real clients complete one agent task) is a manual check.
- T070a (per-call credential headers) is verified: `tests/adapters/test_mcp_tools.py`.
