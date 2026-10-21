# Research: Incoming Messages

## Group behaviour follows a reference Telegram bot

**Decision**: In a group the bot answers only when mentioned, replied to, or sent a command, and it ignores messages that address other bots.

**Rationale**: The bot was asked to behave as the reference agent's Telegram integration does: direct messages, groups, topics, replies and forwarded messages.

**Alternatives considered**: Answering every group message (the reference's own default is configurable; not chosen here).

## Forwarded messages are described to the model

**Decision**: A forwarded message carries a line naming where it came from.

**Rationale**: The reference bot treats a forward as plain text from the person who forwarded it. The operator asked for forwards to be handled, so the model is told the origin.

**Alternatives considered**: none recorded.

## Voice notes are not supported in the first version

**Decision**: A voice note gets a short refusal and is not queued.

**Rationale**: Decided out of scope for the first version by the operator.

**Alternatives considered**: Transcribing through the endpoint's transcription route (left for later).

## Messages that arrive together are joined

**Decision**: A short wait collects related messages into one input, longer for images and for text near Telegram's message length.

**Rationale**: Albums and long pasted texts arrive as several messages; the reference bot batches them the same way.

**Alternatives considered**: none recorded.
