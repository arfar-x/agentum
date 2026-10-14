# Research: Agent Gateway

Each entry: decision, rationale, alternatives. Items marked **verify** are confirmed with a
spike during the first implementation task, before code depends on them.

## R1. Agent loop engine
- **Decision**: LangGraph `StateGraph` built by the gateway (nodes: retrieve, model, tools,
  route), not the prebuilt swarm helper.
- **Rationale**: the gateway needs custom routing between gateway-run and client-run tool calls,
  and the user chose LangGraph. A hand-built graph keeps handoff and approval logic in code we
  can test.
- **Alternatives**: prebuilt react/swarm helpers (less control over executor choice); a
  hand-written loop (no graph tooling, user preferred LangGraph).

## R2. Cross-request state
- **Decision**: no LangGraph checkpointer. Cross-request state is two stored things:
  (a) a **pending record** keyed by the `tool_call_id` the gateway emitted to the client (the
  hidden work, the agent holding the conversation, the action awaiting approval);
  (b) a **thread record** keyed by a user-scoped hash of the visible history, holding the
  active agent.
- **Rationale**: Chat Completions is stateless on the wire; the client resends tool results with
  the `tool_call_id`, which is a natural resume key. Avoids coupling to checkpointer versions.
- **Alternatives**: checkpointer + `interrupt()` (needs a stable thread id the client does not
  send); a conversation id header (clients do not send one).

## R3. Hash chain for thread lookup
- **Decision**: after each response, store the thread under `H(user, visible messages up to and
  including the assistant message just produced)`. On the next request, hash the visible
  messages up to and including the last assistant message and look that up.
- **Rationale**: finds the thread when the client resends history unchanged; a miss falls back
  to a fresh thread seeded from the messages, so edits never error (spec edge case).
- **Alternatives**: trusting a client-sent id (clients do not send one); no state (loses the
  active specialist).

## R4. Executing tools on the client
- **Decision**: emit an assistant message with `tool_calls` using the client's own tool name and
  schema; finish reason `tool_calls`. On the next request the client's `tool` message resumes.
- **Rationale**: this is the standard function-calling loop every client already implements.
- **Open**: **verify** how each target client names MCP tools (prefixes) to finalise
  normalization rules in `domain/toolroute.py`; start with stripping `mcp__<server>__`,
  `<server>_`, `_mcp_<server>`.

## R5. Client-native approval and questions
- **Decision**: a registry of known ask tools (name → argument template → result parser) in
  `gateway.yaml`; fall back to a text approval with an HMAC code.
- **Rationale**: lets the client render its own UI without any client detection (the request's
  tool list is the signal).
- **Open**: **verify** argument shapes for Claude Code `AskUserQuestion`-style, LibreChat
  `ask_user_question`, Hermes `clarify`, opencode `question` against their docs before locking
  the defaults; ship the code fallback regardless.

## R6. MCP tool access per user
- **Decision**: one short-lived MCP client per gateway-run call batch, with the credential
  headers for that user; tool schemas cached globally per server (listing needs no credentials).
- **Rationale**: matches how the tool server already takes per-request credential headers;
  nothing credentialed is cached across users.
- **Alternatives**: a pooled client per user (state to expire and secure).
- **Open**: **verify** the fastmcp client sends custom headers on every request over streamable
  HTTP.

## R7. Passthrough proxy
- **Decision**: httpx streaming reverse proxy for every unhandled `/v1/*` route and for
  non-agent chat/responses requests; swap the gateway key for the upstream key; pass the body
  bytes through untouched.
- **Rationale**: byte-equivalence with the upstream (SC-002, FR-003).

## R8. Wire fidelity
- **Decision**: build responses with the same field set the upstream returns, copying
  `system_fingerprint` and message fields when present; sum `usage` across internal calls;
  emit OpenAI error objects.
- **Verify**: conformance suite replays the official OpenAI SDK against agent and passthrough.

## R9. Activity surfacing
- **Decision**: a built-in instruction block appended to the system prompt, plus
  `reasoning_content` deltas on streams (and the field on non-streamed messages); no header or
  User-Agent logic.
- **Rationale**: user constraint: no client configuration.

## R10. Locks and rate limits
- **Decision**: Redis `SET NX PX` per (user, thread) lock with renewal; token-bucket per key in
  Redis.
- **Alternatives**: Postgres advisory locks (fine for locks, poor for rate limits).

## R11. Credential encryption
- **Decision**: Fernet with a key from `.env.agent-gateway`; ciphertext in Postgres; plaintext
  never logged. Key rotation = re-encrypt command.
- **Rationale**: simple, authenticated, in the standard `cryptography` package.

## R12. Error correction
- **Decision**: pure functions in `domain/corrections.py` map an error kind (unknown tool,
  invalid JSON, schema violation, empty reply) to a concise hint; loop limit and transport
  retry policy in `gateway.yaml`.
