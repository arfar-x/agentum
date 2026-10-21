# Implementation Plan: Account Linking

**Spec**: [`spec.md`](spec.md) | **Status**: As built

## Summary

Unknown users ask for a pairing code in a private chat; the operator approves it from the host with an API key, which is stored encrypted next to the user's Telegram id. Every other capability reads the key through this one.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: python-telegram-bot (handlers), psycopg 3 with a connection pool (storage), cryptography (symmetric encryption of keys), httpx (confirmation message from the operator command)

**Storage**: the bot's own PostgreSQL database; tables for links and pairing codes (see [`data-model.md`](data-model.md))

**Testing**: pytest with in-memory fakes for the store and the vault; Postgres tests against a throwaway container

**Target Platform**: Linux container started from the stack's compose file

**Project Type**: module of a multi-service repository

**Performance Goals**: not stated by the code

**Constraints**: the code is not rate-limited beyond the rules in the spec; the confirmation message uses a 10-second timeout

**Scale/Scope**: not stated by the code

## Constitution Check

- Hexagonal core: linking rules sit in `application/linking.py` over ports; the CLI and the Telegram handler are adapters. Satisfied.
- Secrets in the environment: the encryption key and bot token come from the environment; keys are stored encrypted and never listed. Satisfied.
- Least privilege: the bot's database role reaches only its own database. Satisfied.
- Pinned versions: dependency ranges and the base image tag are not pinned. Divergence recorded below.

## Project Structure

```text
application/linking.py     request, approve, key lookup, unlink
domain/pairing.py          code alphabet, length, lifetimes, normalisation
adapters/outbound/crypto.py   key encryption
adapters/inbound/telegram/bot.py   /start and the gate on unlinked users
cli.py                     operator commands
scripts/pair.sh            combined operator command across the two modules
```

## Configuration

| Setting | Default | Effect |
|---|---|---|
| `CREDS_KEY` | none (required) | Encrypts stored API keys; losing it makes every stored key unreadable |
| `DATABASE_URL` | none (required) | The bot's database |
| `BOT_TOKEN` | none | Used by `pair approve` to send the confirmation; not needed by the other operator commands |

Fixed values: code length 8, lifetime 3,600 s, request interval 600 s, at most 3 live codes.

## Complexity Tracking

| Divergence from the constitution | Why accepted |
|---|---|
| Dependencies are version ranges and the base image tag floats | Same as the gateway module; pinning is not yet done |
