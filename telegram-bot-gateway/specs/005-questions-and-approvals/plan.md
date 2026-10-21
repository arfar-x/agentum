# Implementation Plan: Questions and Approvals

**Spec**: [`spec.md`](spec.md) | **Status**: As built

## Summary

The bot declares one tool, `clarify`. When the endpoint calls it, the turn ends with a question; the runtime sends a card, stores its message id with the open question, and a button handler or the owner's next message answers it as a tool message. The question lives inside the session, so it survives restarts.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: python-telegram-bot (inline keyboards, callback queries)

**Storage**: the open question is part of the session row in the bot's database (capability 002's data model); a card is found by chat id and card message id

**Testing**: pytest; question parsing as a pure function; the runtime and the button handler against fakes

**Target Platform**: Linux container

**Project Type**: module of a multi-service repository

**Performance Goals**: not stated by the code

**Constraints**: callback data is `a:<index>` or `a:o`, within Telegram's 64-byte limit; one open question per session

**Scale/Scope**: not stated by the code

## Constitution Check

- Hexagonal core: the tool declaration and the argument parsing are in `domain/clarify.py`; opening, answering and dropping a question are in `application/turn.py`. Satisfied.
- Two-layer approval: the bot only relays the user's choice; the endpoint keeps its own approval gate and its own write confirmation. The bot never sets a confirmation itself. Satisfied.

## Project Structure

```text
domain/clarify.py         tool declaration, Question, argument parsing
application/turn.py       open question, answer, stale question
adapters/inbound/telegram/runtime.py   card and its keyboard, storing the card id
adapters/inbound/telegram/bot.py       button handler, ownership checks
adapters/outbound/postgres.py          finding a session by its card
```

## Configuration

None. Fixed value: a question is stale after 86,400 seconds.

## Complexity Tracking

| Divergence from the constitution | Why accepted |
|---|---|
| Dependencies are version ranges and the base image tag floats | Same as the gateway module; pinning is not yet done |
