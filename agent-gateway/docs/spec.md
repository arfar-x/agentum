# Agent gateway — software requirements specification

| | |
|---|---|
| **Spec ID** | SPEC-GW-001 |
| **Status** | In progress |
| **Version** | 0.1.0 |
| **Last updated** | 2026-10-14 |
| **Implements** | `agent-gateway/` module |
| **Related** | [`specs/001-agent-gateway/spec.md`](../specs/001-agent-gateway/spec.md), [`README.md`](../README.md) |

Requirement keywords (**MUST**, **MUST NOT**, **SHOULD**, **MAY**) are used as defined in RFC 2119.

This specification is **executable**. Every requirement carries a stable id; `tests/test_spec_coverage.py` fails when a requirement marked *done* has no test claiming it, or when a test claims an id this document does not define. A test claims a requirement with a `# Covers: FR-RTE-01` line. Behavior changes here first, then in code.

---

## 1. Purpose

A self-hosted service that serves configured agents through an OpenAI-compatible API, so any client that speaks that API can use them. It runs the agent loop (retrieval, tools, handoffs, approvals), calls an upstream OpenAI-compatible model, and forwards everything that is not an agent unchanged.

## 2. Glossary

| Term | Meaning |
|---|---|
| **Agent** | A configured behavior addressed by a model name. |
| **Upstream** | The OpenAI-compatible model service the gateway calls. |
| **Gateway-run tool** | A tool call the gateway executes itself. |
| **Client-run tool** | A tool call returned to the client to execute. |
| **Pending turn** | A stored record that lets a later request resume a paused turn. |

## 3. Traceability to the feature spec

| Feature spec | Requirement ids here |
|---|---|
| FR-001, FR-005, SC-001, SC-002 | FR-API-01 … FR-API-06 |
| FR-002, FR-003, FR-004, SC-003 | FR-RTE-01 … FR-RTE-05 |
| FR-006 … FR-009 | FR-AGT-01 … FR-AGT-06, FR-PRM-01 … FR-PRM-04 |
| FR-010, SC-004 | FR-RET-01 … FR-RET-04 |
| FR-011 … FR-013 | FR-TLR-01 … FR-TLR-06, FR-CRD-01 … FR-CRD-05 |
| FR-014 … FR-017 | FR-APR-01 … FR-APR-07 |
| FR-018, FR-019 | FR-HND-01 … FR-HND-03, FR-ACT-01 … FR-ACT-04 |
| FR-020, FR-021, SC-005 | FR-COR-01 … FR-COR-05 |
| FR-022 … FR-026 | FR-AUT-01 … FR-AUT-06, FR-OPS-01 … FR-OPS-04 |
| FR-027 | FR-CFG-01 … FR-CFG-03 |
| FR-028, FR-029 | NFR-ARC-01 … NFR-ARC-04 |

## 4. Acceptance scenarios

| id | Scenario | Status |
|---|---|---|
| AS-01 | **Given** an agent, **when** an unmodified OpenAI SDK streams a chat naming it, **then** it receives a well-formed streamed answer. | planned |
| AS-02 | **Given** a non-agent model, **when** a request is sent, **then** it reaches the upstream unchanged. | planned |
| AS-03 | **Given** a write the gateway would run, **when** the user rejects it, **then** nothing is written. | planned |
| AS-04 | **Given** a specialist handoff, **when** the user follows up, **then** the specialist answers. | planned |
| AS-05 | **Given** a model that emits an unknown tool, **when** the loop runs, **then** the user sees a correct answer and no error. | planned |

## 5. Requirements

### 5.1 API surface — `FR-API`

| id | Requirement | Status |
|---|---|---|
| FR-API-01 | The service MUST expose `GET /v1/models`, `POST /v1/chat/completions` and `POST /v1/responses` in OpenAI's request and response shapes. | planned |
| FR-API-02 | Errors MUST use OpenAI's error object and matching status codes. | planned |
| FR-API-03 | A streamed agent response MUST use server-sent events in OpenAI's chunk shape and end with `[DONE]`. | planned |
| FR-API-04 | `usage` on an agent response MUST be the sum over every upstream call made for the request, and `stream_options.include_usage` MUST be honoured. | planned |
| FR-API-05 | An agent response `model` MUST echo the requested model name. | planned |
| FR-API-06 | `GET /healthz` MUST answer without authentication. | planned |

### 5.2 Routing and passthrough — `FR-RTE`

| id | Requirement | Status |
|---|---|---|
| FR-RTE-01 | A model name equal to an agent id MUST select that agent. | planned |
| FR-RTE-02 | `<agent>:<upstream-model>` MUST select the agent on that upstream model only when the agent allows model override. | done |
| FR-RTE-03 | Any other model name MUST be forwarded to the upstream with the body untouched. | planned |
| FR-RTE-04 | Every other `/v1/*` route MUST be proxied to the upstream, streaming. | planned |
| FR-RTE-05 | `/v1/models` MUST list upstream models and agents, and MUST still list agents when the upstream listing fails. | planned |

### 5.3 Agent definition — `FR-AGT`

| id | Requirement | Status |
|---|---|---|
| FR-AGT-01 | An agent MUST be loaded from a YAML file validated against the agent-file contract; unknown fields MUST be rejected. | planned |
| FR-AGT-02 | Agent ids MUST match `^[a-z0-9][a-z0-9-]*$` and be unique. | done |
| FR-AGT-03 | A handoff target MUST name another loaded agent. | done |
| FR-AGT-04 | A client system prompt MUST be combined per `client_system`: `append` (default), `prepend`, or `ignore`. | done |
| FR-AGT-05 | `${VAR}` references in agent files MUST be resolved from the environment; an unset one MUST fail startup. | planned |
| FR-AGT-06 | A passthrough request MUST NOT receive agent instructions, tools or retrieval. | planned |

### 5.4 Parameters — `FR-PRM`

| id | Requirement | Status |
|---|---|---|
| FR-PRM-01 | Agent `model_parameters` MUST be defaults for every upstream call. | planned |
| FR-PRM-02 | Client parameters MUST override defaults only when `allow_client_overrides` is true. | done |
| FR-PRM-03 | A parameter in `locked_parameters` MUST keep the agent's value and the request MUST still succeed. | done |
| FR-PRM-04 | `n` greater than 1 and `logprobs` on an agent model MUST fail with a 400 naming the parameter. | planned |

### 5.5 Retrieval — `FR-RET`

| id | Requirement | Status |
|---|---|---|
| FR-RET-01 | When `preretrieval.kb.enabled`, catalog hits for the latest user message MUST be in context before the model's first step of the turn. | planned |
| FR-RET-02 | A catalog failure MUST NOT fail the turn; the model MUST be told the catalog was unavailable. | planned |
| FR-RET-03 | With retrieval disabled no catalog call MAY be made. | planned |
| FR-RET-04 | Retrieval MUST NOT run again on the continuation of a turn that is resuming tool results. | planned |

### 5.6 Tool routing — `FR-TLR`

| id | Requirement | Status |
|---|---|---|
| FR-TLR-01 | A tool call MUST be routed in order: client tool, gateway with request credentials, gateway with stored credentials, delegate to the client. | planned |
| FR-TLR-02 | A client tool MUST match an agent tool only by normalized base name or explicit alias. | done |
| FR-TLR-03 | Normalization MUST strip `mcp__<server>__`, a `<server>_` prefix and a `_mcp_<server>` suffix. | done |
| FR-TLR-04 | A client-run call MUST be returned as ordinary `tool_calls` with the client's own tool name, and its result MUST resume the turn. | planned |
| FR-TLR-05 | Hidden gateway-run results from the same step MUST be preserved across a client-run call. | planned |
| FR-TLR-06 | The route MUST be chosen again for every call. | planned |

### 5.7 Credentials — `FR-CRD`

| id | Requirement | Status |
|---|---|---|
| FR-CRD-01 | Request-supplied credentials MUST take precedence over stored ones, per variable. | done |
| FR-CRD-02 | Stored credentials MUST be encrypted at rest. | planned |
| FR-CRD-03 | A user's credentials MUST NOT be usable by another user. | planned |
| FR-CRD-04 | Credentials MUST NOT appear in logs, errors, activity notes or model context. | planned |
| FR-CRD-05 | Only variables a tool server declares MUST be forwarded to it. | done |

### 5.8 Approval — `FR-APR`

| id | Requirement | Status |
|---|---|---|
| FR-APR-01 | A gateway-run call matching an `approval` glob MUST NOT execute until the user approves it. | planned |
| FR-APR-02 | The approval preview MUST state the tool and its arguments. | planned |
| FR-APR-03 | When the client declares a known ask tool, the prompt MUST be emitted through it. | planned |
| FR-APR-04 | Otherwise the prompt MUST be text with a code bound to user, conversation, tool and arguments hash, single-use and expiring. | planned |
| FR-APR-05 | An altered, reused, expired or foreign code MUST be refused with no effect. | planned |
| FR-APR-06 | The model MUST NOT be able to supply `confirm`; the gateway MUST add it only on the approved exact call. | planned |
| FR-APR-07 | A rejected approval MUST return "declined" to the model and execute nothing. | planned |

### 5.9 Handoffs and activity — `FR-HND`, `FR-ACT`

| id | Requirement | Status |
|---|---|---|
| FR-HND-01 | Each configured handoff MUST be exposed to the model as a `transfer_to_<id>` tool. | planned |
| FR-HND-02 | After a handoff, later turns of the same conversation MUST continue with the receiving agent. | planned |
| FR-HND-03 | A conversation whose history cannot be matched MUST start a fresh thread from the visible messages and MUST NOT error. | planned |
| FR-ACT-01 | The system prompt MUST contain the activity instruction block unless the agent sets `activity: quiet`. | planned |
| FR-ACT-02 | Gateway-side events MUST be emitted as `reasoning_content`, never inside `content`. | planned |
| FR-ACT-03 | No behaviour MAY depend on request headers that identify the client. | planned |
| FR-ACT-04 | Long gateway-run tool calls MUST keep a stream alive. | planned |

### 5.10 Self-correction — `FR-COR`

| id | Requirement | Status |
|---|---|---|
| FR-COR-01 | A call to an unknown tool MUST return a concise correction listing valid tools. | planned |
| FR-COR-02 | Invalid tool-argument JSON or a schema violation MUST return a concise correction naming the problem. | planned |
| FR-COR-03 | An empty model reply MUST trigger one correction attempt. | planned |
| FR-COR-04 | Corrections MUST be bounded; exhaustion MUST produce a standard error. | planned |
| FR-COR-05 | Transient upstream failures MUST be retried with backoff before surfacing. | planned |

### 5.11 Authentication and tenancy — `FR-AUT`

| id | Requirement | Status |
|---|---|---|
| FR-AUT-01 | Every route except `/healthz` MUST require a valid bearer key. | planned |
| FR-AUT-02 | Keys MUST be stored hashed and shown once at creation. | planned |
| FR-AUT-03 | Revocation MUST take effect on the next request. | planned |
| FR-AUT-04 | Threads, pending turns and credentials MUST be read only with the owning user's id. | planned |
| FR-AUT-05 | Requests per key MUST be rate-limited; over the limit MUST return 429. | planned |
| FR-AUT-06 | Request bodies over the configured size MUST be refused. | planned |

### 5.12 Operation — `FR-OPS`

| id | Requirement | Status |
|---|---|---|
| FR-OPS-01 | Requests on one conversation MUST be serialized. | planned |
| FR-OPS-02 | Threads and pending turns MUST survive a restart. | planned |
| FR-OPS-03 | Pending turns MUST expire and be pruned. | planned |
| FR-OPS-04 | A command-line tool MUST create, list and revoke keys and set and delete credentials. | planned |

### 5.13 Configuration — `FR-CFG`

| id | Requirement | Status |
|---|---|---|
| FR-CFG-01 | An invalid configuration file MUST stop startup with a message naming file and problem. | planned |
| FR-CFG-02 | Non-secret settings (limits, aliases, ask-tool templates) MUST come from `config/gateway.yaml` with documented defaults. | planned |
| FR-CFG-03 | Secrets MUST come only from the environment. | planned |

### 5.14 Architecture — `NFR-ARC`

| id | Requirement | Status |
|---|---|---|
| NFR-ARC-01 | `domain/` and `application/` MUST NOT import I/O, web, agent-runtime or YAML libraries. | done |
| NFR-ARC-02 | `domain/` MUST NOT depend on `application/`. | done |
| NFR-ARC-03 | Use cases MUST be provable with in-memory fakes. | planned |
| NFR-ARC-04 | The application core MUST take a neutral turn request, independent of HTTP. | planned |

*planned* = specified, not built. *partial* = domain and application exist, adapter wiring does not. *done* = built and claimed by a test.
