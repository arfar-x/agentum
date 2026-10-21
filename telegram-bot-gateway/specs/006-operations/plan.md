# Implementation Plan: Operations

**Spec**: [`spec.md`](spec.md) | **Status**: As built

## Summary

A single container runs the bot. Settings come from one env file. At start it opens its own database on the gateway's PostgreSQL instance, applies its migrations, registers its commands and begins long polling (or a webhook). The stack provides the network, the database role, the make targets and the backup.

## Technical Context

**Language/Version**: Python 3.12 (base image `python:3.12-slim`)

**Primary Dependencies**: python-telegram-bot (application, polling, webhook), psycopg 3 with a pool, httpx, cryptography

**Storage**: a database `telegram_bot` with its own role `telegram_bot` on the gateway's PostgreSQL 16 instance (see [`data-model.md`](data-model.md))

**Testing**: pytest for configuration, the operator commands, start-up wiring (polling and webhook arguments), the health file and the layout of the module; PostgreSQL tests with a throwaway container; the database role script was checked against a throwaway PostgreSQL

**Target Platform**: Linux container started from the stack's compose file

**Project Type**: module of a multi-service repository

**Performance Goals**: not stated by the code

**Constraints**: 256 MB of memory; no published port; unprivileged user; one process (two processes with the same token conflict)

**Scale/Scope**: not stated by the code

## Constitution Check

- Secrets in the environment: all secrets come from the module's env file, which is ignored by version control and copied by the stack's backup. Satisfied.
- No published port, internal-only networks: the service joins the network it shares with the gateway and the gateway's data network, never `backend`. Satisfied.
- Least privilege: the database role reaches only its own database. Satisfied.
- No new infrastructure: it adds a database to an existing instance, no new instance. Satisfied.
- Pinned versions: dependency ranges and the base image tag are not pinned. Divergence recorded below.
- Modules own their configuration: its compose file, env template, make targets, scripts and docs are inside the module; the root reaches it with an include, a make include and a link. Satisfied.

## Project Structure

```text
telegram-bot-gateway/
  Dockerfile, compose.yaml, module.mk, .env.example
  migrations/            numbered SQL files
  scripts/prepare.sh     env file and generated secrets
  scripts/setup-db.sh    database role and database
  scripts/pair.sh        linking across the two modules
  scripts/test-services.sh   Postgres tests against a throwaway container
  src/telegram_bot_gateway/
    config.py, cli.py
    adapters/inbound/telegram/serve.py   start-up, polling or webhook, heartbeat
```

Root changes: an include of the module's compose file, a network shared with the gateway and joined by the gateway, an include of the module's make targets, an ignore entry for the env file, and the backup entry.

## Configuration

See FR-001 of the spec for the full list and defaults. `.env.example` is the tracked template; `.env.telegram-bot-gateway` is the ignored file the service reads.

## Complexity Tracking

| Divergence from the constitution | Why accepted |
|---|---|
| Dependencies are version ranges and the base image tag floats | Same as the gateway module; pinning is not yet done |
