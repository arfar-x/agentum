# Implementation Plan: Incoming Messages

**Spec**: [`spec.md`](spec.md) | **Status**: As built

## Summary

Handlers read each Telegram update, decide whether it is a trigger with a pure rule, turn the message and what it replies to or forwards into text and images, and hand the result to the runtime with a wait that depends on its kind.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: python-telegram-bot (updates, entities, file downloads)

**Storage**: message texts in the bot's database (see capability 002's `data-model.md`)

**Testing**: pytest; updates built from Telegram's JSON, a fake bot object for file downloads

**Target Platform**: Linux container

**Project Type**: module of a multi-service repository

**Performance Goals**: not stated by the code

**Constraints**: files are downloaded whole into memory and encoded as data URLs; the 4,096 most recent update ids are remembered in memory

**Scale/Scope**: not stated by the code

## Constitution Check

- Hexagonal core: the trigger rule, the reply and forward text, the merge and the waits are in `domain/`; Telegram objects are read only in `adapters/`. Satisfied.
- Least privilege: updates other than messages and button taps are not requested from Telegram. Satisfied.

## Project Structure

```text
domain/triggers.py     the trigger rule over a few flags
domain/content.py      reply and forward text, merge, item
domain/batching.py     the waits
domain/session.py      topic id rules
adapters/inbound/telegram/bot.py     handlers, flags from entities, reply lookup
adapters/inbound/telegram/media.py   images, documents, stickers, places, forward origin
```

## Configuration

None. All values here are fixed: waits of 0.18, 0.24, 0.3, 1.0 and 0.8 seconds; 100,000 bytes for inlined text; 4,096 remembered update ids.

## Complexity Tracking

| Divergence from the constitution | Why accepted |
|---|---|
| Dependencies are version ranges and the base image tag floats | Same as the gateway module; pinning is not yet done |
