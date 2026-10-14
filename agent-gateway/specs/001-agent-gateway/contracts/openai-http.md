# Contract: OpenAI-compatible HTTP

Base path `/v1`. Auth: `Authorization: Bearer <gateway key>`. Errors use OpenAI's
`{"error": {"message", "type", "param", "code"}}` with matching status codes
(401 bad key, 404 unknown model, 400 invalid request, 429 rate limit, 502/503 upstream).

| Route | Behaviour |
|---|---|
| `GET /v1/models` | Upstream models (as the upstream lists them) plus one entry per agent: `{id, object: "model", created, owned_by}`. If the upstream list fails, agents are still listed. |
| `GET /v1/models/{id}` | Agent entry or proxied upstream entry. |
| `POST /v1/chat/completions` | `model` an agent → agent loop. Otherwise proxied unchanged. Supports `messages`, `stream`, `stream_options.include_usage`, `tools`, `tool_choice`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `stop`, `seed`, `reasoning_effort`, `response_format`, `user`. `n>1` and `logprobs` on agents → 400 naming the parameter. |
| `POST /v1/responses` | Same routing. `input`, `instructions`, `previous_response_id`, `tools`, `reasoning`, `stream`. Server keeps the response → thread mapping. |
| `ANY /v1/*` | Streaming reverse proxy to the upstream with the key swapped. |
| `GET /healthz` | Liveness, no auth. |

## Request headers the gateway reads
- `Authorization` (required).
- `X-Agent-Skills-Env-<VAR>`-style credential headers, passed to tool servers for gateway-run
  calls; the exact prefix is configurable per tool server in `mcp-servers.yaml`.

No header selects a client profile.

## Agent response rules
- `model` echoes the requested name; `id`, `created`, `object` follow OpenAI's shapes.
- `usage` is the sum of all upstream calls made for the request.
- Server-side activity appears as `delta.reasoning_content` on streams and as
  `message.reasoning_content` otherwise; never inside `content`.
- A turn that needs the client ends with `finish_reason: "tool_calls"` and ordinary
  `tool_calls`.
- Approval fallback: a short text message with a one-use code; the user's next message
  `approve <code>` or `reject <code>` resumes it.
