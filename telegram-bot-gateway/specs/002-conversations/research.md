# Research: Conversations

## Plain Chat Completions, not a vendor-specific API

**Decision**: The bot sends standard Chat Completions requests and reads standard streams, with one declared tool.

**Rationale**: The bot must work with any model or agent behind any OpenAI-compatible endpoint. The endpoint it was built for drops images on its other route, and keeps images on this one.

**Alternatives considered**: The Responses API (rejected: images are dropped there).

## The history is stored and replayed exactly

**Decision**: Assistant messages, including tool calls, are stored as returned and sent back unchanged.

**Rationale**: The endpoint this bot was built for finds a conversation again by hashing the visible history it receives; any change to an earlier message starts a new thread there.

**Alternatives considered**: none recorded.

## The system note is sent with each request and never stored

**Decision**: The note is added in front of the history on every request and is not part of the stored history.

**Rationale**: The endpoint treats a client's system message as lower-priority client instructions and leaves it out of the history it matches on. Keeping it out of the stored history lets its wording change without touching earlier conversations.

**Alternatives considered**: none recorded.

## Messages sent during a turn wait; they never interrupt

**Decision**: Messages arriving during a turn are queued and answered together after it; only `/stop` cancels.

**Rationale**: A turn may run write tools on the endpoint's side, so aborting one can hide a side effect.

**Alternatives considered**: Restarting the turn with the new message (rejected for that reason); injecting the message after the next tool call (not chosen).

## State lives in the bot's own database on the gateway's Postgres instance

**Decision**: Sessions and links are stored in a separate database with its own role on the Postgres instance the gateway already runs.

**Rationale**: One instance to back up, and the bot's role reaches only its own database. The other instances belong to services the bot must not reach.

**Alternatives considered**: A file-based database inside the bot's container (the first design, replaced at the operator's request).

## Each user has their own session even in a group

**Decision**: In a group the session key includes the user.

**Rationale**: A conversation in the endpoint belongs to one key, and each user has their own.

**Alternatives considered**: One shared session per chat (rejected for that reason).
