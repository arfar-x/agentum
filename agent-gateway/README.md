# agent-gateway

Serve your agents through an OpenAI-compatible API, so any client can use them: coding agents,
the OpenAI SDK, chat UIs. Pick an agent by name as the `model`. Use any other model name
and the request goes to the upstream model unchanged.

## What a client sees

- `GET /v1/models` lists the upstream models and the agents.
- `POST /v1/chat/completions` and `POST /v1/responses` run an agent when `model` names one:
  system prompts, reasoning effort, sampling parameters, streaming and the client's own tools all
  work. `model: "<agent>:<upstream-model>"` runs the agent on another model, if the agent allows.
- Model entries carry the context window (`context_window`, `context_length`, `max_model_len`): an
  agent's `context_window`, else `models.context_windows` in `config/gateway.yaml`, else
  `MODEL_CONTEXT_TOKENS`.
- Every other `/v1/*` route is proxied to the upstream.
- Errors, usage and streaming use OpenAI's shapes.

## Run

```bash
cp .env.example .env            # fill in UPSTREAM_BASE_URL, DATABASE_URL, REDIS_URL, CREDS_KEY, APPROVAL_SECRET
python -m agent_gateway check
python -m agent_gateway serve
python -m agent_gateway key create you@example.org   # prints the key once
```

`CREDS_KEY` is a Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
The gateway needs Postgres and Redis. Put TLS in front of it: it publishes plain HTTP.

## Connect a client

Use the gateway's address with `/v1` as the base URL and your key as the API key.

```python
from openai import OpenAI
client = OpenAI(base_url="https://gateway.example/v1", api_key="agw_...")
client.chat.completions.create(model="product-assistant", messages=[{"role": "user", "content": "hi"}])
```

Any client that accepts a custom OpenAI-compatible provider works the same way: set the base URL
and key, then choose an agent from the model list.

## How tools and credentials work

For each tool call an agent makes, in order:

1. **The gateway runs it with credentials sent on the request** (headers named in
   `config/mcp-servers.yaml`, e.g. `X-Agent-Skills-Env-JIRA_USERNAME`). Set them in your client's
   custom-headers setting, from your local environment.
2. **The gateway runs it with your stored credentials** (`creds set`), encrypted at rest.
3. Otherwise the tool is not offered to the agent, and the agent tells you which credentials are
   missing and **asks whether you would rather use your client's own tools** (for example its
   connectors). Only if you say yes does your client's equivalent tool get used, for the rest of
   that conversation. Your client's permission prompt gates writes.

The agent's own tools always take precedence: when the gateway can run one, a client tool with the
same job is not offered in its place.

Writes the gateway runs are shown to you first. If your client has a question tool
(`AskUserQuestion`, `ask_user_question`, `question`, `clarify`) the prompt appears there; otherwise
reply `approve <code>` or `reject <code>` to the message that shows the code.

## Configure

`config/agents/*.yaml` define agents, `config/mcp-servers.yaml` the tool servers and
`config/gateway.yaml` the limits. See `docs/spec.md` for what each setting guarantees, and
`specs/001-agent-gateway/contracts/agent-file.schema.yaml` for the agent file.

## Test

```bash
python -m pytest
scripts/test-services.sh
```
