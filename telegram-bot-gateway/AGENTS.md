# Agent instructions: telegram-bot-gateway

This module is written to become its own repository. Inside it, this file governs; the root `CLAUDE.md`
only says how it is wired into agentflow.

## Rules

- **The spec comes first.** Change [`docs/spec.md`](docs/spec.md) (a reviewed diff), then the code. Every
  requirement has an id and a status; a test claims one with a `# Covers: FR-XXX-NN` line.
  `tests/test_spec_coverage.py` fails on a `done` requirement without a test, a claim of an undefined id, or a
  test for something still marked `planned`.
- **Hexagonal.** `domain/` and `application/` do no I/O and import nothing from `adapters/`
  (`tests/test_boundaries.py`). New outside dependencies are a port in `application/ports.py` and an adapter.
- **Endpoint-agnostic.** Talk plain Chat Completions. Nothing may depend on agent-gateway: no import of it, no
  request header or field only it understands. What agent-gateway does with the `clarify` tool is its contract,
  not this module's code.
- **The key is the user.** Each linked Telegram user has their own API key. Never share one key across users.
- **Secrets** come from the environment only and never reach a log, a reply or an error. API keys are stored
  encrypted with `CREDS_KEY`. `/whoami` and `links` never show one.
- **No allowlist.** Who may use the bot is decided by linking, not by a list of Telegram ids.
- **Queue, never interrupt.** A running turn is cancelled only by `/stop`.
- The module publishes no port and joins only `telegram-gateway` and `gateway-data`, never `backend`.

## Specs

The behaviour of the module is specified under [`specs/`](specs/glossary.md), one numbered directory per
capability, in the repository constitution's order of work (`.specify/memory/constitution.md`): change the
spec first, then plan, tasks, code and tests, in the same change. [`docs/spec.md`](docs/spec.md) stays the
test-gated registry of requirement ids and maps them to the specs. Defects found when the specs were written
are listed in each spec's `Known Defects and Gaps` and are scheduled for correction; the strict
expected-failure tests in `tests/test_known_defects.py` turn into failures when a defect is fixed, and the
marker is then removed with the fix.

## Commands

```bash
. .venv/bin/activate && python -m pytest        # unit tests
scripts/test-services.sh -q                      # Postgres tests in a throwaway container
```

Commit messages follow Conventional Commits, like the root repository.
