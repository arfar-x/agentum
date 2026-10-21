# Implementation Plan: Replies and Feedback

**Spec**: [`spec.md`](spec.md) | **Status**: As built

## Summary

Pure text functions turn the endpoint's Markdown into Telegram's MarkdownV2, a plain fallback, and parts within the length limit. A sender applies them, with fallbacks for the failures Telegram makes routine. The runtime owns the typing loop, the reaction marks and the progress message.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: python-telegram-bot (sending, editing, reactions, chat actions)

**Storage**: the plain text of sent messages in the bot's database (capability 002's data model)

**Testing**: pytest; a fake bot object records every call; formatting and splitting are tested as pure functions

**Target Platform**: Linux container

**Project Type**: module of a multi-service repository

**Performance Goals**: not stated by the code

**Constraints**: 4,096 UTF-16 units per message; a 16-unit reserve for the suffix; typing every 2 s; progress edits every 2 s; reaction delay 10 s

**Scale/Scope**: not stated by the code

## Constitution Check

- Hexagonal core: formatting, splitting and the plain fallback are in `domain/formatting.py`; the sender is an adapter. Satisfied.
- Provable with fakes: the fallbacks are tested against a fake bot that fails on demand. Satisfied.

## Project Structure

```text
domain/formatting.py   MarkdownV2, plain text, splitting, UTF-16 length
adapters/inbound/telegram/sender.py    send, edit, fallbacks, targets
adapters/inbound/telegram/runtime.py   typing, reaction marks, progress
```

## Configuration

| Setting | Default | Effect |
|---|---|---|
| `REACTIONS` | true | Allows the 👀 reaction in the two cases of the spec |
| `REACTION_DELAY` | 10 | Seconds a turn runs before its last message gets 👀 |
| `SHOW_PROGRESS` | false | Shows the endpoint's reasoning while it works |

Fixed values: 4,096 units, 16-unit reserve, 5 send attempts, 0.5 s added to a flood wait, 600 characters of reasoning, 2 s typing and progress interval.

## Complexity Tracking

| Divergence from the constitution | Why accepted |
|---|---|
| Dependencies are version ranges and the base image tag floats | Same as the gateway module; pinning is not yet done |
