# Agent instructions

## Overview

`agent-gateway` serves configured agents through an OpenAI-compatible API (Chat Completions,
Responses, Models). A client picks an agent by putting its id in `model`; any other model name
goes straight to the upstream model, along with every other `/v1/*` route. The service runs the
agent loop itself (retrieval, tools, handoffs, approvals) and calls an upstream OpenAI-compatible
model. Nothing here names a specific model or organization; those live in the environment and in
`config/`.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e '.[serve,dev]'
python -m pytest                 # unit, application, HTTP and SDK-conformance tests; no services
scripts/test-services.sh         # also the Postgres/Redis tests, in throwaway containers
python -m agent_gateway check    # validate config/ and the environment
python -m agent_gateway serve    # run the server
python -m agent_gateway key register-llm --owner <sub>   # model key from stdin
python -m agent_gateway key create --owner <sub>         # only with GATEWAY_ISSUE_KEYS=true
python -m agent_gateway key list --owner <sub>
python -m agent_gateway creds set --owner <sub> agent-skills JIRA_PASSWORD   # value from stdin
python -m agent_gateway audit list --since 1d
python -m agent_gateway directory sync|show|orphans   # the copy of the identity provider's people
python -m agent_gateway owner purge --owner <sub> --yes
```

## Architecture

Hexagonal, like a small `kb`:

- `domain/` pure rules: routing, parameter merge, tool routing, approval codes, history hashing,
  corrections, prompts, identity (principal, keys, names).
- `application/` ports (`ports.py`), the turn logic as small nodes (`turn.py`) and use cases.
  No I/O, no web framework, no agent-runtime library: `tests/test_boundaries.py` enforces it.
- `adapters/inbound/` the OpenAI HTTP surface (FastAPI) and the CLI.
- `adapters/outbound/` LangGraph driver, upstream LLM (httpx), MCP tools, Postgres, Redis,
  encryption, config loading.

Every decision lives in `application/turn.py`. The LangGraph graph only owns the order nodes run
in, and `run_sequentially` is the reference driver the tests use.

Cross-request state has two parts: a thread record found by hashing the visible history (so the
active agent survives a handoff) and a paused-turn record keyed by the `tool_call_id` the gateway
handed to the client (or by an approval code), which restores the hidden work on resume.

## Specs

`docs/spec.md` is the spec of record. Every requirement has an id and a status, and
`tests/test_spec_coverage.py` fails when a `done` requirement has no test claiming it
(`# Covers: FR-...`), or when a test claims an unknown id, or claims a requirement still marked
`planned`. Change the spec first, then the code. `specs/001-agent-gateway/` holds the Spec Kit
working documents for the first build.

## Rules

- Secrets come only from the environment. `config/` holds no credential values.
- A gateway-run write is never executed without the user's approval, and the model can never
  supply `confirm` itself.
- Credentials never reach the model, the response, or a log.
- No behavior may depend on request headers that identify the client.
