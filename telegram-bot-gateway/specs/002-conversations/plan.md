# Implementation Plan: Conversations

**Spec**: [`spec.md`](spec.md) | **Status**: As built

## Summary

A turn is a pure use case over ports: take the stored session, add the user's message, call the endpoint until it answers or asks a question, and hand the outcome back. A runtime in the Telegram adapter batches incoming messages, queues them while a turn runs, cancels on request and saves the session. The endpoint client streams the answer.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: httpx (streamed endpoint client), psycopg 3 with a connection pool (sessions), python-telegram-bot (handlers, runtime chrome)

**Storage**: the bot's own PostgreSQL database; a session is one row with the history as JSON (see [`data-model.md`](data-model.md))

**Testing**: pytest and pytest-asyncio; in-memory store and a scripted endpoint; a mock HTTP transport for the endpoint client; Postgres tests for the stored shapes

**Target Platform**: Linux container

**Project Type**: module of a multi-service repository

**Performance Goals**: not stated by the code

**Constraints**: at most 8 endpoint calls per turn; request timeout 600 s by default; connection 10 s; one process, state held in memory only for batching, queueing and running turns

**Scale/Scope**: not stated by the code

## Constitution Check

- Hexagonal core: `domain/` (content, settings, session key, prompt text) and `application/` (turn, commands) import no I/O; `adapters/` hold Telegram, the endpoint client and storage; the boundary test enforces it. Satisfied.
- Provable with fakes: every use case runs against an in-memory store and a scripted endpoint. Satisfied.
- Typed data at boundaries: the application records are typed; the history stays the endpoint's own JSON shape. Satisfied.
- Secrets: the user's key is decrypted per turn and passed only as a header. Satisfied.

## Project Structure

```text
domain/content.py        message content, merging, image dropping
domain/settings.py       setting names, ranges, parsing
domain/session.py        session key, topic id rules
domain/prompt.py         the system note
application/turn.py      begin (open question, history), run (rounds)
application/commands.py  setting changes and the /settings text
application/model.py     Session, Pending
application/ports.py     Store, Llm, Vault
adapters/inbound/telegram/runtime.py   batching, queue, stop, one turn end to end
adapters/outbound/openai.py            streamed endpoint client
adapters/outbound/postgres.py          sessions
```

## Configuration

| Setting | Default | Effect |
|---|---|---|
| `OPENAI_BASE_URL` | none (required) | The endpoint |
| `OPENAI_API_KEY` | empty | Used only to list models before a user is linked |
| `DEFAULT_MODEL` | none (required) | The model a session uses until the user sets one |
| `REQUEST_TIMEOUT` | 600 | Seconds without data before a request is abandoned |
| `SHOW_PROGRESS` | false | Shows the endpoint's reasoning while it works (capability 004) |

Fixed values: 8 rounds per turn; 40 model buttons; 86,400 s lifetime of an open question; 10 s connection timeout.

## Complexity Tracking

| Divergence from the constitution | Why accepted |
|---|---|
| Dependencies are version ranges and the base image tag floats | Same as the gateway module; pinning is not yet done |
