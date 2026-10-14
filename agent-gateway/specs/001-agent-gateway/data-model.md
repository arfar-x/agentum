# Data model: Agent Gateway

All persisted rows carry `user_id` and are only read with the authenticated user's id.

## Agent (from file; not persisted)
| Field | Rules |
|---|---|
| `id` | unique, `^[a-z0-9][a-z0-9-]*$`; what clients put in `model` |
| `name`, `description` | shown in model list |
| `model` | upstream model, `${ENV}` interpolation allowed |
| `model_parameters` | defaults (temperature, reasoning_effort, ...) |
| `allow_client_overrides` | bool, default true |
| `locked_parameters` | subset of parameter names |
| `allow_model_override` | bool, default true |
| `client_system` | `append` (default) \| `prepend` \| `ignore` |
| `client_tools` | `all` (default) \| `none` \| list of names |
| `instructions` | text |
| `tools` | list of `{server, include[], approval[]}` globs |
| `interaction` | `[ask_user]` or empty |
| `preretrieval.kb` | `{enabled, limit, types, query_rewrite}` |
| `handoffs` | list of `{to, description}`; `to` must name another agent |
| `activity` | `narrate` (default) \| `quiet` |

Validation: unknown fields rejected; every `to` resolves; every `server` exists in
`mcp-servers.yaml`; globs must match at least one known tool (warning, not error, when the
server is unreachable at startup).

## ApiKey (`api_keys`)
`id`, `user_id`, `label`, `key_hash` (SHA-256 of a random secret, secret shown once),
`created_at`, `revoked_at`, `last_used_at`. States: active → revoked.

## UserCredential (`user_credentials`)
`user_id`, `server`, `var`, `ciphertext`, `updated_at`. Unique `(user_id, server, var)`.

## Thread (`threads`)
`user_id`, `history_hash`, `active_agent`, `updated_at`. Unique `(user_id, history_hash)`.
Pruned after a retention window (default 30 days).

## PendingTurn (`pending_turns`)
`user_id`, `call_id` (the tool_call_id emitted to the client), `kind`
(`client_tool` \| `approval` \| `question`), `agent_id`, `hidden_messages` (JSON: assistant
tool-call messages and gateway tool results produced earlier in the step), `action`
(for `approval`: tool, arguments, arguments_hash), `code_hash` (text-fallback approval),
`expires_at`, `consumed_at`. States: open → consumed \| expired. A consumed or expired row can
never resume.

## Redis keys (ephemeral)
- `lock:{user_id}:{thread_key}` — per-conversation lock with TTL.
- `rl:{key_id}:{window}` — rate-limit counter.

## Domain values (not persisted)
- `TurnRequest`: user, model route, messages, params, client tools, stream flag, request
  credentials.
- `Route`: `agent` (id, optional upstream override) \| `passthrough`.
- `ToolRoute`: `client(name)` \| `gateway(server, tool, credentials_from)` \| `delegate(name)`.
- `ActivityEvent`: `handoff`, `retrieval`, `tool_started`, `tool_finished`, `waiting`.
- `Correction`: kind + concise hint text for the model.
