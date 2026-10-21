# Research: Replies and Feedback

## MarkdownV2 with a plain-text fallback

**Decision**: Answers are converted to Telegram's MarkdownV2 following the reference bot's rules, and sent as plain text when Telegram rejects the formatting.

**Rationale**: The operator chose MarkdownV2 as the reference bot does, over the simpler HTML form, accepting its stricter escaping.

**Alternatives considered**: Telegram HTML (not chosen).

## The answer is sent once it is complete

**Decision**: The answer is not edited into view as it streams.

**Rationale**: Live editing costs an edit loop and a rate budget per chat; the typing indicator covers the wait.

**Alternatives considered**: Streaming into one message that is edited every fraction of a second (cut by the operator).

## A reaction only when it tells something

**Decision**: 👀 appears only on a queued message or when a turn takes long, and is removed afterwards.

**Rationale**: A reaction on every message is redundant; the operator wanted reactions kept for when they matter.

**Alternatives considered**: A reaction on every message and a result mark (👍/👎) after it (replaced); no reactions at all (not wanted).

## Answers are short and use emojis

**Decision**: A system note asks the model for short answers, Telegram-friendly formatting and a few emojis.

**Rationale**: Long content reads badly in a chat; the operator asked for answers that suit Telegram, with emojis and markup to make them easy to scan.

**Alternatives considered**: none recorded.
