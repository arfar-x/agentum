# Implementation Plan: Agent Gateway

**Branch**: `feat/agent-gateway` | **Date**: 2026-10-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `agent-gateway/specs/001-agent-gateway/spec.md`

## Summary

A Python service that exposes configured agents as models on an OpenAI-compatible HTTP API and
forwards everything else to the upstream model. A hexagonal core (pure domain and application
layers) decides routing, tool executor, approval, parameter merge and error correction. A
LangGraph runtime in the adapters runs the agent loop. State lives in Postgres and Redis. The
detailed design, phases and verification were approved in
`/home/alireza/.claude/plans/you-know-these-agents-sprightly-pike.md`; this plan turns it into
buildable parts.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI + uvicorn (inbound), httpx (upstream + proxy), LangGraph +
langchain-core (agent loop), `mcp` client via fastmcp (tool servers), pydantic 2 (boundary
models), PyYAML (config), cryptography/Fernet (credential encryption), psycopg 3 (Postgres),
redis-py (locks, rate limits)

**Storage**: Postgres (keys, encrypted credentials, thread state, pending approvals); Redis
(per-thread locks, rate-limit counters)

**Testing**: pytest, pytest-asyncio; domain and application tests use in-memory fakes; adapters
tested against fake MCP servers (in-process FastMCP), a scripted stub LLM and throwaway
Postgres/Redis; OpenAI SDK conformance suite

**Target Platform**: Linux container on the existing compose host; reached by external clients
over the network behind the operator's TLS

**Project Type**: web-service (single project, future submodule)

**Performance Goals**: non-agent passthrough adds < 50 ms median (SC-003); agent first-token
latency dominated by the upstream model plus one catalog lookup

**Constraints**: wire-compatible with OpenAI for chat completions, responses and models; no
client detection; no secret in logs; every state row scoped to a user

**Scale/Scope**: tens of users, a handful of agents, tens of concurrent conversations

## Constitution Check

*Against `.specify/memory/constitution.md` v1.1.0.*

| Principle | Result |
|---|---|
| I. Configuration over code | Pass. No upstream patching. The module is generic, names no model or organisation; the model is `${DEFAULT_MODEL}`. agentflow-specific content is configuration under `agent-gateway/config/`. |
| II. Spec of record leads | Pass. `agent-gateway/docs/spec.md` carries FR/NFR/AS ids with statuses; `tests/test_spec_coverage.py` enforces `done` ⇒ claimed. |
| III. Hexagonal, provable core | Pass. `domain/` and `application/` import no I/O, web framework, agent runtime or YAML; `tests/test_boundaries.py` enforces. LangGraph lives only in `adapters/outbound/langgraph_runtime`. |
| IV. Secrets and internal services | Pass with a recorded amendment: the gateway is the second published port, authenticated per key, user-scoped, rate-limited, and does not join `backend`. Secrets only in `.env.agent-gateway`. |
| V. Least privilege | Pass. Gateway-run writes need the gateway approval gate and the toolset's own `--confirm`; client-run writes are gated by the client's prompt (recorded as an accepted limit). The gateway holds no source credentials unless a user stored them, encrypted. |
| No new infrastructure | Justified: Postgres and Redis, both lightweight, both owned by the gateway; Redis is needed for cross-process locks and rate limits, Postgres for durable state. |

No unjustified violations. Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
agent-gateway/specs/001-agent-gateway/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openai-http.md
│   ├── agent-file.schema.yaml
│   └── internal-ports.md
└── tasks.md
```

### Source Code

```text
agent-gateway/
├── pyproject.toml  Dockerfile  Makefile  README.md  AGENTS.md  .gitignore
├── config/
│   ├── agents/*.yaml          # agent definitions
│   ├── mcp-servers.yaml       # tool servers
│   └── gateway.yaml           # non-secret settings, tool aliases, ask-tool templates
├── docs/spec.md               # spec of record
├── specs/001-agent-gateway/   # this feature
├── scripts/
├── src/agent_gateway/
│   ├── domain/
│   │   ├── agent.py  routing.py  params.py  toolroute.py  approval.py
│   │   ├── history.py  activity.py  corrections.py  messages.py
│   ├── application/
│   │   ├── ports.py
│   │   └── use_cases/ run_turn.py  resume.py  list_models.py  keys.py  credentials.py
│   ├── adapters/
│   │   ├── inbound/openai_http/  app.py  chat.py  responses.py  models.py  proxy.py  errors.py  auth.py
│   │   ├── inbound/cli.py
│   │   └── outbound/
│   │       ├── langgraph_runtime/  graph.py  nodes.py
│   │       ├── mcp_tools.py  upstream_llm.py  config_files.py
│   │       ├── postgres_store.py  redis_locks.py  crypto.py
│   ├── container.py
│   └── config.py
├── migrations/
└── tests/  domain/ application/ adapters/ integration/ conformance/
           test_boundaries.py  test_spec_coverage.py
```

**Structure Decision**: one self-contained project in `agent-gateway/`, modelled on `kb/`.
agentflow wires it with compose services and `.env.agent-gateway`; nothing under
`agent-gateway/` imports from or references the repository root.

## Complexity Tracking

None.
