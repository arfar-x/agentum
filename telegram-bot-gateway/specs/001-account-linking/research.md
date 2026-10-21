# Research: Account Linking

## One API key per Telegram user

**Decision**: Every linked user holds their own API key of the endpoint.

**Rationale**: In the endpoint this bot was built for, the key is the user: stored credentials, conversation threads and approvals belong to it. A shared key would merge every Telegram user into one identity.

**Alternatives considered**: One shared bot key (rejected for that reason).

## No allowlist of Telegram ids

**Decision**: Who may use the bot is decided by having a link, which comes from the stack's own user management, not from a list kept in the bot.

**Rationale**: Users come from the stack's user management. A second list in the bot would be a second place to maintain.

**Alternatives considered**: A configured list of allowed Telegram ids (rejected).

## The operator approves every link

**Decision**: A link exists only after the operator approves a pairing code.

**Rationale**: The username in a request is free text. The operator's check is the only thing that stops someone from claiming another person's account.

**Alternatives considered**: The user pastes their own key into the chat (rejected: the key would pass through Telegram); the operator binds a Telegram id by hand (rejected: it needs the operator to look up numeric ids).

## Linking is a replaceable step

**Decision**: Linking is kept behind a small interface (find a user's key, approve, unlink) so another identity source can replace pairing.

**Rationale**: A single sign-on service is expected later, and nothing outside linking should change when it arrives.

**Alternatives considered**: none recorded.
