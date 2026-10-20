# telegram-bot-gateway

A Telegram bot that is a client of any OpenAI-compatible Chat Completions endpoint. It is built for
[`agent-gateway`](../agent-gateway/README.md) (an agent is a `model` name) and depends on nothing
gateway-specific. Spec of record: [`docs/spec.md`](docs/spec.md).

## What it does

- **DMs, groups and forum topics.** In a group it answers when mentioned, replied to, or given a command.
  Each user has their own session per chat and topic.
- **Replies and forwards.** The model is told what was replied to (the user's own quote first) and where a
  forwarded message came from.
- **Media.** Photos, image files and albums go to the model as images; small text files are inlined; other
  files, stickers and locations become a text note. Voice notes are not supported yet.
- **Typing, and sparing reactions.** Typing is shown while it works. A 👀 appears only when it helps: on a message waiting behind a running turn, or when an answer takes more than 10 seconds; it is removed afterwards.
- **Telegram-aware answers.** Every request carries a short system note: the user is in Telegram, answers should be brief and use Telegram-friendly Markdown and a few emojis.
- **Questions and approvals.** The bot declares one tool, `clarify`. A question, or an approval the endpoint
  wants (agent-gateway asks before it writes), appears as a message with buttons; only the owner can tap.
- **Settings.** `/model`, `/reasoning`, `/set`, `/unset`, `/settings` change what each request sends.
- **Busy sessions.** Messages sent during a turn are queued and merged into the next one; `/stop` cancels.

## Who may use it

Nobody is allowed by default and there is no allowlist. A user is **linked** to one API key of the endpoint:
they send `/start <username>`, receive a code, and the operator approves it with `make telegram-pair`. The
key is the user's own, so with agent-gateway their stored credentials, threads and approvals stay theirs.
See [`docs/OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md).

## Run it (inside the agentflow stack)

```bash
make telegram-setup   # creates telegram-bot-gateway/.env.telegram-bot-gateway and the database
# set BOT_TOKEN and DEFAULT_MODEL in that file, then:
make telegram-up
```

Variables are listed in [`.env.example`](.env.example).

## Develop

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[serve,dev]'
python -m pytest                 # unit tests, no services
scripts/test-services.sh -q      # the Postgres tests, against a throwaway container
```

Layout: `domain/` (pure rules) -> `application/` (use cases over ports) -> `adapters/` (Telegram, the
endpoint, Postgres). `tests/test_boundaries.py` fails if `domain/` or `application/` do I/O, and
`tests/test_spec_coverage.py` fails when a requirement marked `done` has no test claiming it.
