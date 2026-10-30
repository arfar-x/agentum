# Agent gateway — software requirements specification

| | |
|---|---|
| **Spec ID** | SPEC-GW-001 |
| **Status** | In progress |
| **Version** | 0.9.0 |
| **Last updated** | 2026-10-25 |
| **Implements** | `agent-gateway/` module |
| **Related** | [`specs/001-agent-gateway/spec.md`](../specs/001-agent-gateway/spec.md), [`specs/003-identity-access/`](../../specs/003-identity-access/spec.md), [`README.md`](../README.md) |

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
| **Unavailable tool** | An agent tool that neither the client nor the gateway can run for this request; it is hidden from the model. |
| **Client-run tool** | A tool call returned to the client to execute. |
| **Pending turn** | A stored record that lets a later request resume a paused turn. |
| **Person** | A human or service account at the identity provider, known by its account id (`sub`). |
| **Principal** | The authenticated caller of one request: account id, username, groups, kind, key and key scope. |
| **Directory copy** | The gateway's periodically refreshed copy of the identity provider's users, groups and roles. |
| **Identity provider** | The external system that knows who people are (Keycloak today), reached only through the provider ports of FR-IDP-01. |
| **Model key** | A person's own key for the upstream model, registered with the gateway. |
| **Issued key** | An `agk_` key minted by the gateway; exists only when `GATEWAY_ISSUE_KEYS` is true. |

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
| (added in 0.7.0) | FR-MEM-01 … FR-MEM-06 |
| (added in 0.8.0 and 0.9.0, spec 003; FR-ENT-01 … 10 are `done`, the rest `planned`) | FR-IDP-01 … 07, FR-IDN-01 … 08, FR-ATH-01 … 14, FR-KEY-01 … 08, FR-UPS-01 … 08, FR-ENT-01 … 10, FR-CRD-06 … 09, FR-OPS-05 … 08, FR-CFG-04, FR-PRT-01 … 13, FR-AUD-01 … 06, FR-KBI-01 … 04 |

## 4. Acceptance scenarios

| id | Scenario | Status |
|---|---|---|
| AS-01 | **Given** an agent, **when** an unmodified OpenAI SDK streams a chat naming it, **then** it receives a well-formed streamed answer. | done |
| AS-02 | **Given** a non-agent model, **when** a request is sent, **then** it reaches the upstream unchanged. | done |
| AS-03 | **Given** a write the gateway would run, **when** the user rejects it, **then** nothing is written. | done |
| AS-04 | **Given** a specialist handoff, **when** the user follows up, **then** the specialist answers. | done |
| AS-05 | **Given** a model that emits an unknown tool, **when** the loop runs, **then** the user sees a correct answer and no error. | done |

## 5. Requirements

### 5.1 API surface — `FR-API`

| id | Requirement | Status |
|---|---|---|
| FR-API-01 | The service MUST expose `GET /v1/models`, `POST /v1/chat/completions` and `POST /v1/responses` in OpenAI's request and response shapes. | done |
| FR-API-02 | Errors MUST use OpenAI's error object and matching status codes. | done |
| FR-API-03 | A streamed agent response MUST use server-sent events in OpenAI's chunk shape and end with `[DONE]`. | done |
| FR-API-04 | `usage` on an agent response MUST be the sum over every upstream call made for the request, and `stream_options.include_usage` MUST be honoured. | done |
| FR-API-05 | An agent response `model` MUST echo the requested model name. | done |
| FR-API-06 | `GET /healthz` MUST answer without authentication. | done |

### 5.2 Routing and passthrough — `FR-RTE`

| id | Requirement | Status |
|---|---|---|
| FR-RTE-01 | A model name equal to an agent id MUST select that agent. | done |
| FR-RTE-02 | `<agent>:<upstream-model>` MUST select the agent on that upstream model only when the agent allows model override. | done |
| FR-RTE-03 | Any other model name MUST be forwarded to the upstream with the body untouched. | done |
| FR-RTE-04 | Every other `/v1/*` route MUST be proxied to the upstream, streaming. | done |
| FR-RTE-05 | `/v1/models` MUST list upstream models and agents, and MUST still list agents when the upstream listing fails. | done |
| FR-RTE-06 | Model entries MUST carry the context window in tokens (`context_window`, `context_length`, `max_model_len`) when known: an agent's own setting, else a per-model table, else the configured default. An upstream entry that already states one MUST be left untouched. | done |
| FR-RTE-07 | A pass-through path MUST be refused (OpenAI-shaped 404) when any segment is `.` or `..` in raw or percent-encoded form (including double encoding), when it contains a backslash, a NUL or a control character, or when the resulting upstream URL does not stay under the upstream base path. Nothing is sent upstream. | done |
| FR-RTE-08 | A pass-through request MUST be refused with 403 `permission_denied` for a caller whose key scope names agents or forbids writes (an issued key with a restricted scope, or a model-key login in `read-only` mode). Token callers and unrestricted keys are unaffected. | done |
| FR-RTE-09 | A pass-through request MUST forward only an allow-list of request headers (`content-type`, `accept`, `user-agent`, `openai-organization`, `openai-project`, `openai-beta`, `x-request-id`, `x-client-request-id`); the caller's `Authorization` is replaced by the person's model key, and cookies, tool-credential headers and every other header are dropped. Hop-by-hop headers MUST be dropped in both directions. | done |

### 5.3 Agent definition — `FR-AGT`

| id | Requirement | Status |
|---|---|---|
| FR-AGT-01 | An agent MUST be loaded from a YAML file validated against the agent-file contract; unknown fields MUST be rejected. | done |
| FR-AGT-02 | Agent ids MUST match `^[a-z0-9][a-z0-9-]*$` and be unique. | done |
| FR-AGT-03 | A handoff target MUST name another loaded agent. | done |
| FR-AGT-04 | A client system prompt MUST be combined per `client_system`: `append` (default), `prepend`, or `ignore`. When appended, it MUST be marked as lower priority than the agent's instructions and followed by a reminder of the agent's identity. | done |
| FR-AGT-05 | `${VAR}` references in agent files MUST be resolved from the environment; an unset one MUST fail startup. | done |
| FR-AGT-06 | A passthrough request MUST NOT receive agent instructions, tools or retrieval. | done |
| FR-AGT-07 | A client system prompt longer than `client_prompt_limit_chars` MUST be compacted to about `client_prompt_target_chars` before it is placed, at lower priority, in the agent's prompt. Shorter prompts MUST be used as sent. | done |
| FR-AGT-08 | A compacted prompt MUST be cached per user and prompt for `client_prompt_cache_seconds`, so later turns carrying the same prompt make no compaction call; another user's cache entry MUST NOT be used. Receiving a different client prompt MUST invalidate the user's older entries beyond `client_prompt_cache_entries` (default 1, so only the latest is kept). | done |
| FR-AGT-09 | A failed or useless compaction (error, empty, or longer than the original) MUST NOT fail the turn: the prompt is truncated to the target instead. | done |

### 5.4 Parameters — `FR-PRM`

| id | Requirement | Status |
|---|---|---|
| FR-PRM-01 | Agent `model_parameters` MUST be defaults for every upstream call. | done |
| FR-PRM-02 | Client parameters MUST override defaults only when `allow_client_overrides` is true. | done |
| FR-PRM-03 | A parameter in `locked_parameters` MUST keep the agent's value and the request MUST still succeed. | done |
| FR-PRM-04 | `n` greater than 1 and `logprobs` on an agent model MUST fail with a 400 naming the parameter. | done |

### 5.5 Retrieval — `FR-RET`

| id | Requirement | Status |
|---|---|---|
| FR-RET-01 | When `preretrieval.kb.enabled`, catalog hits for the latest user message MUST be in context before the model's first step of the turn. | done |
| FR-RET-02 | A catalog failure MUST NOT fail the turn; the model MUST be told the catalog was unavailable. | done |
| FR-RET-03 | With retrieval disabled no catalog call MAY be made. | done |
| FR-RET-04 | Retrieval MUST NOT run again on the continuation of a turn that is resuming tool results. | done |

### 5.6 Tool routing — `FR-TLR`

| id | Requirement | Status |
|---|---|---|
| FR-TLR-01 | An agent's own tools take precedence. A tool call MUST be routed to the gateway when the tool's server needs no credentials, or the request or the store supplies them (request first, then stored). Otherwise the client's equivalent tool MAY be used only after the user has opted in (FR-TLR-09). | done |
| FR-TLR-02 | A client tool MUST match an agent tool only by normalized base name or explicit alias. | done |
| FR-TLR-03 | Normalization MUST strip `mcp__<server>__`, a `<server>_` prefix and a `_mcp_<server>` suffix. | done |
| FR-TLR-04 | A client-run call MUST be returned as ordinary `tool_calls` with the client's own tool name, and its result MUST resume the turn. | done |
| FR-TLR-05 | Hidden gateway-run results from the same step MUST be preserved across a client-run call. | done |
| FR-TLR-06 | The route MUST be chosen again for every call. | done |
| FR-TLR-08 | When the gateway can run an agent tool, a client tool with the same name or an equivalent one MUST NOT be offered to the model in its place. | done |
| FR-TLR-07 | A tool the gateway cannot run (its server needs credentials the request and the store lack) MUST NOT be offered to the model, and neither MUST the client's equivalent (matched by name, or by a term the tool server lists in `client_terms`, such as `jira`) until the user opts in; the model MUST instead be told, in one note, which tools are unavailable, how the user can enable them, and that it must ask the user before using the client's own tools or connectors for them. | done |
| FR-TLR-09 | The model MUST be able to record that the user chose to use the client's own tools (`use_client_tools`); from then on, for the rest of that conversation, the client's equivalents of unavailable tools MUST be offered. A new conversation MUST start without the opt-in. | done |

### 5.7 Credentials — `FR-CRD`

| id | Requirement | Status |
|---|---|---|
| FR-CRD-01 | Request-supplied credentials MUST take precedence over stored ones, per variable. | done |
| FR-CRD-02 | Stored credentials MUST be encrypted at rest. | done |
| FR-CRD-03 | A person's credentials MUST NOT be usable by another person (by account id). | done |
| FR-CRD-04 | Credentials MUST NOT appear in logs, errors, activity notes or model context. | done |
| FR-CRD-05 | Only variables a tool server declares MUST be forwarded to it. | done |
| FR-CRD-06 | The gateway MUST be able to list the variable names a person has stored for a server, and MUST NOT return or log any value when it does. | done |
| FR-CRD-07 | A single stored variable MUST be deletable without touching the person's other variables. | done |
| FR-CRD-08 | A tool server MAY declare each credential with a label, whether it is secret and whether it is required, and MAY declare a read-only test tool. A variable that is not declared MUST be refused when stored. A plain name (the form used today) MUST remain valid. | done |
| FR-CRD-09 | Stored credentials MUST be keyed by the person's identity (FR-IDN-01). The person's model key MUST be shown as server `llm`, variable `API_KEY` in listings, computed from the key store, and MUST NOT be stored a second time. | done |

### 5.8 Approval — `FR-APR`

| id | Requirement | Status |
|---|---|---|
| FR-APR-01 | A gateway-run call matching an `approval` glob MUST NOT execute until the user approves it. | done |
| FR-APR-02 | The approval preview MUST state the tool and its arguments. | done |
| FR-APR-03 | When the client declares a known ask tool, the prompt MUST be emitted through it. | done |
| FR-APR-04 | Otherwise the prompt MUST be text with a code bound to user, conversation, tool and arguments hash, single-use and expiring. | done |
| FR-APR-05 | An altered, reused, expired or foreign code MUST be refused with no effect. | done |
| FR-APR-06 | The model MUST NOT be able to supply `confirm`; the gateway MUST add it only on the approved exact call. | done |
| FR-APR-07 | A rejected approval MUST return "declined" to the model and execute nothing. | done |

### 5.9 Handoffs and activity — `FR-HND`, `FR-ACT`

| id | Requirement | Status |
|---|---|---|
| FR-HND-01 | Each configured handoff MUST be exposed to the model as a `transfer_to_<id>` tool. | done |
| FR-HND-02 | After a handoff, later turns of the same conversation MUST continue with the receiving agent. | done |
| FR-HND-03 | A conversation whose history cannot be matched MUST start a fresh thread from the visible messages and MUST NOT error. | done |
| FR-ACT-01 | The system prompt MUST contain the activity instruction block unless the agent sets `activity: quiet`. | done |
| FR-ACT-02 | Gateway-side events MUST be emitted as `reasoning_content`, never inside `content`. | done |
| FR-ACT-03 | No behaviour MAY depend on request headers that identify the client. | done |
| FR-ACT-04 | Long gateway-run tool calls MUST keep a stream alive. | done |

### 5.10 Self-correction — `FR-COR`

| id | Requirement | Status |
|---|---|---|
| FR-COR-01 | A call to an unknown tool MUST return a concise correction listing valid tools. | done |
| FR-COR-02 | Invalid tool-argument JSON or a schema violation MUST return a concise correction naming the problem. | done |
| FR-COR-03 | An empty model reply MUST trigger one correction attempt. | done |
| FR-COR-04 | Corrections MUST be bounded; exhaustion MUST produce a standard error. | done |
| FR-COR-05 | Transient upstream failures MUST be retried with backoff before surfacing. | done |

### 5.11 Authentication and tenancy — `FR-AUT`

| id | Requirement | Status |
|---|---|---|
| FR-AUT-01 | Every route except `/healthz` MUST require a valid bearer key. | done |
| FR-AUT-02 | Issued keys MUST be stored hashed and shown once at creation (FR-KEY-05); a person's own model key is hashed and encrypted (FR-KEY-01). | done |
| FR-AUT-03 | Revocation MUST take effect on the next request. | done |
| FR-AUT-04 | Threads, pending turns and credentials MUST be read only with the owning account id (FR-IDN-01). | done |
| FR-AUT-05 | Requests per key MUST be rate-limited; over the limit MUST return 429. | done |
| FR-AUT-06 | Request bodies over the configured size MUST be refused. | done |

### 5.12 Operation — `FR-OPS`

| id | Requirement | Status |
|---|---|---|
| FR-OPS-01 | Requests on one conversation MUST be serialized. | done |
| FR-OPS-02 | Threads and pending turns MUST survive a restart. | done |
| FR-OPS-03 | Pending turns MUST expire and be pruned. | done |
| FR-OPS-04 | A command-line tool MUST create, list and revoke keys and set and delete credentials. Superseded by FR-OPS-05 to FR-OPS-08, which keep it true for keys by owner. | done |
| FR-OPS-05 | The command-line tool MUST register a person's model key (read from standard input, never from an argument), create an issued key only when issuing is enabled, list a person's keys without secrets, and revoke any key. | done |
| FR-OPS-06 | The command-line tool MUST list a person's stored credential names, set one variable (value from standard input), and delete one variable or all of a server's variables. | done |
| FR-OPS-07 | The command-line tool MUST list audit events (with filters by actor, action and time), run a directory sync now, show the directory copy with its age, and list owners that have stored data but no directory entry. | done |
| FR-OPS-08 | The command-line tool MUST be able to purge everything stored for one owner (keys, credentials, threads, pending turns, responses), reporting counts, and MUST record the purge in the audit log. | done |

### 5.13 Configuration — `FR-CFG`

| id | Requirement | Status |
|---|---|---|
| FR-CFG-01 | An invalid configuration file MUST stop startup with a message naming file and problem. | done |
| FR-CFG-02 | Non-secret settings (limits, aliases, ask-tool templates) MUST come from `config/gateway.yaml` with documented defaults. | done |
| FR-CFG-03 | Secrets MUST come only from the environment. | done |
| FR-CFG-04 | The identity settings (`GATEWAY_ISSUE_KEYS`, `GATEWAY_MODEL_KEY_LOGIN`, `IDENTITY_PROVIDER`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_TTL`, `DIRECTORY_CLIENT_ID`, `DIRECTORY_CLIENT_SECRET`, `DIRECTORY_SYNC_SECONDS`, `DIRECTORY_MAX_STALE_SECONDS`, `PORTAL_CLIENT_ID`, `PORTAL_CLIENT_SECRET`, `PORTAL_SESSION_SECRET`, `PORTAL_BASE_URL`, `KB_TRUST_SECRET`, `AUDIT_RETENTION_DAYS`) MUST come from the environment, have the documented defaults, and be validated at startup; a missing or inconsistent one MUST stop startup with a message naming it and no secret value. | partial |

### 5.14 Architecture — `NFR-ARC`

| id | Requirement | Status |
|---|---|---|
| NFR-ARC-01 | `domain/` and `application/` MUST NOT import I/O, web, agent-runtime or YAML libraries. | done |
| NFR-ARC-02 | `domain/` MUST NOT depend on `application/`. | done |
| NFR-ARC-03 | Use cases MUST be provable with in-memory fakes. | done |
| NFR-ARC-04 | The application core MUST take a neutral turn request, independent of HTTP. | done |

### 5.15 Memory of earlier work — `FR-MEM`

A client sees only the gateway's visible replies, so on the next request the model has no sign that its answer came from tool work the gateway did. Asked how it knows, it concludes it guessed.

| id | Requirement | Status |
|---|---|---|
| FR-MEM-01 | After a turn, the gateway MUST record each tool call it ran itself during that turn: the agent, the tool, its arguments, and whether it worked. A catalog lookup made before the model answered MUST be recorded the same way. | done |
| FR-MEM-02 | The record MUST NOT contain what a tool returned, and MUST NOT include calls run by the client, which are already in the visible history. | done |
| FR-MEM-03 | The record MUST be stored with the thread, encrypted, so that only the owning user's id reads it. | done |
| FR-MEM-04 | The record MUST be bounded: each argument value is shortened, secrets-named arguments are replaced, at most 40 entries are kept and the oldest go first. | done |
| FR-MEM-05 | On a later turn of the same thread the model MUST be told, in a note outside the visible messages, what the gateway ran earlier, that the user did not see it, and not to say it guessed when the list shows it looked the answer up. | done |
| FR-MEM-06 | The note MUST NOT appear in any response, and a conversation whose history cannot be matched (FR-HND-03) MUST start without one. | done |

### 5.16 Identity — `FR-IDN`

Who a caller is. Design and decisions: [`specs/003-identity-access/`](../../specs/003-identity-access/spec.md).

| id | Requirement | Status |
|---|---|---|
| FR-IDN-01 | A person MUST be identified by the identity provider's account id (`sub`). Threads, pending turns, responses, keys, stored credentials and audit entries MUST be owned by that id. | done |
| FR-IDN-02 | Authentication MUST produce one `Principal` per request (account id, username, groups, roles, kind `user` or `service`, key id, key scope), carried on the neutral turn request. The application core MUST NOT read request headers. | done |
| FR-IDN-03 | The gateway MUST keep a copy of the identity provider's users (account id, username, kind, groups, roles, enabled, sync time), refreshed every `DIRECTORY_SYNC_SECONDS` through the identity-provider port (FR-IDP-01) using read-only access. A refresh MUST replace the whole copy in one transaction and MUST change nothing if any part of it fails. | done |
| FR-IDN-04 | A failed refresh MUST keep the previous copy, be retried at the next interval, and be audited once per streak of failures. A caller whose account is not in the copy MAY trigger one refresh, at most once every 30 seconds. | done |
| FR-IDN-05 | The copy MUST hold no email address and no attribute other than those listed in FR-IDN-03. | done |
| FR-IDN-06 | An account that is a service account at the identity provider MUST be a principal of kind `service`; its groups are those assigned to it. | done |
| FR-IDN-07 | The migration that introduces this model MUST discard existing keys, stored credentials, threads, pending turns and responses; it MUST NOT try to map old user names to accounts. | done |
| FR-IDN-08 | A group or role MUST be a non-empty name of at most 128 characters with no comma and no control character, compared exactly and case-sensitively, with no inheritance. Invalid names received from outside MUST be dropped with a log line; in configuration they MUST be rejected. | done |

### 5.16.1 Identity provider interface — `FR-IDP`

| id | Requirement | Status |
|---|---|---|
| FR-IDP-01 | The application layer MUST define one provider-neutral port bundle: a token verifier returning an `Identity` (account id, username, groups, roles, kind), a directory source returning every `Account` (adding `enabled`), and a portal login (authorize URL, complete a code exchange into an `Identity`). `domain/` and `application/` MUST NOT name a provider or read its settings. | done |
| FR-IDP-02 | Verifier, directory source and portal login MUST return cleaned group and role names (FR-IDN-08) and a `kind` of `user` or `service`. Built-in or provider-internal roles MUST NOT be returned. | done |
| FR-IDP-03 | `IDENTITY_PROVIDER` MUST select the adapter; `keycloak` is the only implemented value and any other MUST stop startup naming the allowed values. A provider's own settings MUST be read, and validated, only by its adapter. | done |
| FR-IDP-04 | Account management (creating or deleting accounts, assigning groups or roles, passwords, multi-factor sign-in) MUST NOT be part of the interface, and an adapter MUST make only read requests to the provider apart from the sign-in and token exchanges themselves. | done |
| FR-IDP-05 | A provider's adapter MUST live in its own package under `adapters/outbound/identity/<provider>/`, and nothing outside it, the composition root and its tests MUST import it. | done |
| FR-IDP-06 | A reusable provider contract test class MUST exist, and each adapter MUST pass it with no network: good, expired, wrong-audience, wrong-issuer and unsigned tokens; key rotation and provider outage; `fetch_all` shape (disabled, service and role-only accounts) and all-or-nothing behavior; a login round trip with wrong-verifier, wrong-nonce and reused-code refusals; and no secret in errors. | done |
| FR-IDP-07 | Adding a provider MUST require no change to `domain/`, `application/`, the portal, the authenticator or another adapter. | done |

### 5.17 Authentication of requests — `FR-ATH`

| id | Requirement | Status |
|---|---|---|
| FR-ATH-01 | A bearer MUST be looked up first as a key (by hash) and only then verified as a token. Neither matching MUST give 401. A key bearer is an issued key (while issuing is on) or, per `GATEWAY_MODEL_KEY_LOGIN` (FR-ATH-13), a person's own model key. | done |
| FR-ATH-02 | A token MUST be refused unless its signature verifies against the issuer's published keys with an allowed algorithm (RS256 or ES256; never `none` or a shared-secret algorithm), `iss` equals `OIDC_ISSUER`, `aud` contains `OIDC_AUDIENCE`, and `exp` and `nbf` hold (60 seconds of leeway). A bearer over 8 KiB MUST be refused. | done |
| FR-ATH-03 | The issuer's signing keys MUST be cached for `OIDC_JWKS_TTL`, refetched at most once every 30 seconds when a token names an unknown key id, and kept in use when a refetch fails. | done |
| FR-ATH-04 | A token caller's principal MUST take the account id, username, groups and roles from the `Identity` the token verifier returns, with the default key scope. Groups and roles are read from the identity provider and MUST NOT be created, changed or assigned by the gateway. | done |
| FR-ATH-05 | A key caller's principal MUST be built from the owner's directory entry; an unknown or disabled owner MUST be refused with 401. | done |
| FR-ATH-06 | A key caller whose directory entry is older than `DIRECTORY_MAX_STALE_SECONDS` MUST be refused with 503 and a retry hint, not 401. | done |
| FR-ATH-07 | A token caller whose fresh directory entry says the account is disabled MUST be refused. A missing entry MUST NOT refuse a token. | done |
| FR-ATH-08 | A revoked or expired key MUST be refused on the next request, and an issued key MUST be refused whenever `GATEWAY_ISSUE_KEYS` is off. | done |
| FR-ATH-09 | Every route except `/healthz` and the portal's own sign-in routes MUST require authentication. | done |
| FR-ATH-10 | Requests MUST be rate-limited per person, whichever bearer is used; over the limit MUST return 429. | done |
| FR-ATH-11 | A refusal MUST use OpenAI's error object, MUST NOT say which check failed, MUST NOT contain the bearer, and MUST be audited (best effort) with a reason code. The one exception is `model_key_shared` (FR-ATH-14). | done |
| FR-ATH-12 | Startup MUST fail, naming the setting (validated by the selected provider's adapter, FR-IDP-03), when `OIDC_ISSUER`, `OIDC_AUDIENCE` or `DIRECTORY_CLIENT_SECRET` is missing, when the stale limit is below twice the sync interval, or when the portal is enabled without its client secret, session secret (at least 32 bytes) or issuer. | done |
| FR-ATH-13 | `GATEWAY_MODEL_KEY_LOGIN` (`off`, `read-only` or `full`; default `read-only`) MUST decide whether a person's own model key presented as the bearer identifies that person. `off`: such a string never authenticates. `read-only`: it authenticates with `KeyScope(write=False)`, so approval-gated write tools are never offered. `full`: the default scope. Keycloak tokens and issued keys are unaffected. An unrecognised value MUST stop startup. | done |
| FR-ATH-14 | A model-key string identifies a person only when exactly one owner has it registered as an active model key. When two or more different owners have, it MUST be refused with 401, the audit reason `model_key_shared` and the message "This key is shared by several people, so it cannot identify you. Sign in with Keycloak or use a gateway key."; an unknown string gets the ordinary generic 401. Once only one owner remains the string is a valid login again. | done |

### 5.18 Keys — `FR-KEY`

| id | Requirement | Status |
|---|---|---|
| FR-KEY-01 | Registering a person's model key MUST store its SHA-256 hash and an encrypted copy, MUST keep a short non-secret fragment for recognition, and MUST NOT return or list the key afterwards. | done |
| FR-KEY-02 | A person MUST have at most one active model key. Registering another MUST revoke the first in the same transaction, and revocation MUST destroy the stored encrypted copy. | done |
| FR-KEY-03 | An issued key's hash MUST be unique among active keys. A model key MAY be registered by several people (the same string under different owners); registering one MUST never be refused because someone else holds it, and MUST NOT reveal whether anyone does. | done |
| FR-KEY-04 | Issuing a gateway key MUST be refused, in the command-line tool and in the portal, unless `GATEWAY_ISSUE_KEYS` is true. | done |
| FR-KEY-05 | An issued key MUST be `agk_` plus at least 32 random bytes, shown once, stored only as a hash, owned by a person, with an expiry and a scope (agents and write). | done |
| FR-KEY-06 | Listing keys MUST show id, kind, label, fragment, scope, dates and status, and never a secret. | done |
| FR-KEY-07 | A person MUST be able to revoke their own keys and an operator any key; revocation is final. | done |
| FR-KEY-08 | The last-used time of a key MUST be updated at most once a minute. | done |

### 5.19 The model key a call is made with — `FR-UPS`

| id | Requirement | Status |
|---|---|---|
| FR-UPS-01 | Every call made to the upstream model on a person's behalf (agent turns, prompt compaction, the model listing and pass-through requests) MUST use that person's registered key. | done |
| FR-UPS-02 | A person with no registered key MUST get 403 `upstream_key_required` with the place to register one, with nothing sent upstream. There is no shared fallback key and no `UPSTREAM_API_KEY` setting. | done |
| FR-UPS-03 | The pass-through MUST replace the caller's `Authorization` with the resolved key and MUST NOT forward the caller's own token or gateway key. | done |
| FR-UPS-04 | A model key MUST NOT appear in a log, a response, an error, a stored thread or pending turn, or the model's context. | done |
| FR-UPS-05 | A 401 or 403 from the upstream on a person's own key MUST be answered with 502 `upstream_key_rejected` saying the key was refused and where to replace it, and MUST be audited. | done |
| FR-UPS-06 | A resumed turn MUST use the key of the person who resumes it; no key is stored with a paused turn. | done |
| FR-UPS-07 | A key used as the bearer MUST be the key used upstream for that request. | done |
| FR-UPS-08 | Stored tool credentials and model keys MUST be read and written by the person's account id (`Principal.user_id`) only, never by the key presented. Two people holding the same model-key string keep separate credentials, and a turn uses only the running person's own. | done |

### 5.20 Entitlements — `FR-ENT`

| id | Requirement | Status |
|---|---|---|
| FR-ENT-01 | An agent file MAY carry `access: {groups, roles, write_groups, write_roles}`. A caller may use the agent when it has any listed group or any listed role; the write rule is the same over `write_groups` and `write_roles`. With no block, or with neither `groups` nor `roles`, every signed-in person may use the agent; with neither write list, anyone who may use the agent may use its write tools. | done |
| FR-ENT-02 | A present list MUST be non-empty with valid, unique names; an empty `access` block and unknown keys MUST be rejected at startup. | done |
| FR-ENT-03 | `/v1/models` MUST list only the agents the caller may use, and MUST still list upstream models unchanged. | done |
| FR-ENT-04 | A request naming an agent the caller may not use MUST be answered exactly as for an unknown model. | done |
| FR-ENT-05 | A handoff to an agent the caller may not use MUST NOT be offered to the model, and a thread whose active agent the caller may no longer use MUST NOT continue with it. | done |
| FR-ENT-06 | Resuming a paused turn MUST check again that the caller may use the agent and the tool. | done |
| FR-ENT-07 | An approval-gated (write) tool MUST NOT be offered to the model unless the caller matches the agent's `write_groups` or `write_roles` (when either is present) and the key scope allows writes. | done |
| FR-ENT-08 | A key's scope MUST narrow, never widen: only agents named by both the scope (when it names any) and permitted by the caller's groups and roles are usable. | done |
| FR-ENT-09 | Access MUST be evaluated for each request from the principal and MUST NOT be stored in a thread. | done |
| FR-ENT-10 | A request for a model that is not an agent MUST NOT be subject to agent access rules. | done |

### 5.21 Portal — `FR-PRT`

| id | Requirement | Status |
|---|---|---|
| FR-PRT-01 | A web portal MUST be served under `/portal` by the gateway when `PORTAL_BASE_URL` is set, and MUST answer 404 otherwise. | done |
| FR-PRT-02 | Signing in MUST use the issuer's authorization code flow with PKCE (S256), a state and a nonce, from a confidential client. | done |
| FR-PRT-03 | A session MUST be a signed, HttpOnly, SameSite cookie with an absolute lifetime of 8 hours and an idle limit of 30 minutes, carrying no stored secret. | done |
| FR-PRT-04 | Each request MUST end the session when the person is disabled or absent in a fresh directory entry. | done |
| FR-PRT-05 | Every state-changing request MUST carry the session's CSRF token and an origin matching `PORTAL_BASE_URL`, or be refused with 403 and no effect. | done |
| FR-PRT-06 | A person MUST be able to register, replace and revoke their model key in the portal; the key is write-only. | done |
| FR-PRT-07 | A person MUST be able to set, replace and delete each declared tool credential in the portal, and see only which are set. | done |
| FR-PRT-08 | Where a server declares a test tool, the portal MUST offer a test that uses the person's own stored credentials and reports success or a masked error. | done |
| FR-PRT-09 | The issued-keys page MUST exist only when `GATEWAY_ISSUE_KEYS` is true; a key is shown once, at creation. | done |
| FR-PRT-10 | No portal response MUST contain a stored secret; pages MUST be `no-store`. | done |
| FR-PRT-11 | Portal responses MUST carry a restrictive content security policy, frame denial and no referrer, and templates MUST escape all values. | done |
| FR-PRT-12 | The portal MUST act only on the signed-in person's own data, and every change MUST be audited in the same transaction. | done |
| FR-PRT-13 | The model-key page MUST say the key is used to call the model; that, depending on `GATEWAY_MODEL_KEY_LOGIN`, it can also act as the person's login only when nobody else registered the same string and only for read-only use; and that sharing a key with colleagues disables it as a login. Registering a key someone else holds MUST succeed and MUST NOT reveal that. | done |

### 5.22 Audit — `FR-AUD`

| id | Requirement | Status |
|---|---|---|
| FR-AUD-01 | The gateway MUST keep an audit log of events with time, actor, action, target and a small detail. | done |
| FR-AUD-02 | It MUST record key registration, issue and revocation, credential set, delete and test, refused requests, first use of a client by a person, directory failures and recoveries, upstream key rejections and owner purges. | partial |
| FR-AUD-03 | An entry MUST NOT contain a token, key, credential value or password in any field; detail MUST be bounded to 2 KiB. | done |
| FR-AUD-04 | A change and its audit entry MUST be written together; the audit entry of a refusal MUST be best effort and never turn a refusal into an error. | done |
| FR-AUD-05 | Identical events (same actor, action, target) within a minute MUST be folded into one entry with a count. | done |
| FR-AUD-06 | Entries older than `AUDIT_RETENTION_DAYS` MUST be deleted. | done |

### 5.23 Identity forwarded to tool servers — `FR-KBI`

| id | Requirement | Status |
|---|---|---|
| FR-KBI-01 | A tool server configured with `forward_identity: true` MUST receive the caller's groups in `X-KB-Groups` and the shared secret in `X-KB-Trust`, for gateway-run calls and for pre-retrieval. | done |
| FR-KBI-02 | No other server MUST receive them; the secret MUST NOT appear in a log, a response or the model's context. | done |
| FR-KBI-03 | Group names containing a comma or a control character MUST be dropped, at most 64 names and 4 KiB sent. With no principal, or no secret configured, neither header is sent. | done |
| FR-KBI-04 | A service principal's groups MUST be forwarded like a person's. | done |

### 5.24 Amendments to existing requirements

These rows stay as they are until the units below land; the unit that builds the new behavior edits
the row's wording in the same commit.

| Existing | Becomes | Unit |
|---|---|---|
| FR-AUT-01 "valid bearer key" | a valid key or token (FR-ATH-01) | U2 |
| FR-AUT-02 "keys hashed, shown once" | applies to issued keys (FR-KEY-05); model keys are hashed and encrypted (FR-KEY-01) | U1 (done) |
| FR-AUT-03 "revocation on the next request" | unchanged in effect for keys; adds the person-disabled rules (FR-ATH-07, FR-ATH-08) | U2 |
| FR-AUT-04 "owning user's id" | the owner is the account id (FR-IDN-01) | U1 |
| FR-AUT-05 "per key" | per person (FR-ATH-10) | U2 |
| FR-OPS-04 "create, list and revoke keys and set and delete credentials" | replaced by FR-OPS-05 to FR-OPS-08 | U1 |
| FR-CRD-03 "another user" | another person, by account id | U1 |
| FR-ACT-03 "no behaviour depends on client-identifying headers" | still true: authorisation depends on the bearer, never on a header that names the client | none |

*planned* = specified, not built. *partial* = domain and application exist, adapter wiring does not. *done* = built and claimed by a test.
