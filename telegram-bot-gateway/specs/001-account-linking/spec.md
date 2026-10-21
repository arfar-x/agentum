# Feature Specification: Account Linking

**Created**: 2026-10-21

**Status**: Accepted

**Input**: Existing system behavior

Terms follow [`../glossary.md`](../glossary.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask to be linked (Priority: P1)

A person who is not yet linked opens a private chat with the bot and asks to be linked, giving the username the endpoint knows them by. The bot gives them a pairing code to hand to the operator.

**Why this priority**: Nobody can use the bot until they are linked, and the bot has no list of allowed users; linking is the only way in.

**Independent Test**: In a private chat with an unlinked user, send `/start ann@example.org` and receive a code of 8 characters; nothing reaches the endpoint.

**Acceptance Scenarios**:

1. **Given** an unlinked user in a private chat, **When** they send `/start <username>`, **Then** the bot replies with a pairing code and says it is valid for one hour and that the operator links them.
2. **Given** an unlinked user, **When** they send `/start` with no username, **Then** the bot replies that they are not linked and tells them to send `/start <your username>`.
3. **Given** a user who received a code less than 10 minutes ago, **When** they send `/start <username>` again, **Then** the bot replies that they asked recently and creates no new code.
4. **Given** a user with three codes that are less than one hour old and the newest is at least 10 minutes old, **When** they send `/start <username>`, **Then** the bot replies that they have too many open codes and creates no new code.
5. **Given** an unlinked user in a group, **When** they send `/start <username>`, **Then** the bot replies that they must message it privately and creates no code.
6. **Given** a linked user, **When** they send `/start`, **Then** the bot replies with the username they are linked as and invites them to send a message.

---

### User Story 2 - Operator approves a code (Priority: P1)

The operator sees who asked for each open code, checks that the person is who they say they are, and approves the code with an API key for that username. The user is told they are linked.

**Why this priority**: The operator's approval is the only thing that stops a person claiming another person's account.

**Independent Test**: Create a code as an unlinked user, list open codes as the operator, approve the code with an API key, and confirm the user can now chat and receives a confirmation message.

**Acceptance Scenarios**:

1. **Given** open codes, **When** the operator lists them, **Then** each shows the code, the username given, the Telegram name and the Telegram id; with no open codes the operator sees "no open codes".
2. **Given** a live code, **When** the operator approves it with an API key, **Then** the user is linked, the code is removed, the key is stored encrypted, and the user receives "You are linked. Send me a message to begin."
3. **Given** a code typed in lower case or with spaces or hyphens, **When** the operator approves it, **Then** it is matched as if typed in upper case without spaces or hyphens.
4. **Given** a code that is unknown or at least one hour old, **When** the operator approves or shows it, **Then** the command fails with "unknown or expired code" and nothing changes.
5. **Given** no API key on standard input, **When** the operator approves a code, **Then** the command fails with "expected the API key on standard input" and the code stays open.
6. **Given** a user who is already linked, **When** the operator approves a new code from that user, **Then** the link is replaced: the new username, name and key take over.
7. **Given** the operator's combined command, **When** it runs for a live code, **Then** it issues an endpoint key for the username in the code, labelled `telegram`, and approves the code with it, without the key being shown or stored anywhere else.

---

### User Story 3 - Unlinked users are kept out (Priority: P1)

Whatever an unlinked person sends, nothing reaches the endpoint, and they are told how to get linked.

**Why this priority**: Users come from the stack's own user management; a message from a stranger must not run work under anyone's key.

**Independent Test**: As an unlinked user, send a message, a command and a button tap; confirm the endpoint receives nothing and each gets the linking reply.

**Acceptance Scenarios**:

1. **Given** an unlinked user in a private chat, **When** they send any message that is not a command, **Then** the bot replies "You are not linked yet. Send /start <your username> here and I will give you a code for the operator." and the message is not queued.
2. **Given** an unlinked user in a group who triggers the bot, **When** the bot replies, **Then** it says "Message me privately to link your account first.", and it says this at most once per 10 minutes for that user.
3. **Given** an unlinked user, **When** they use `/new`, `/model`, `/reasoning`, `/set`, `/unset` or `/settings`, **Then** they get the linking reply and nothing changes.
4. **Given** an unlinked user, **When** they use `/stop`, `/help` or `/whoami`, **Then** the command answers (`/whoami` shows that they are not linked).
5. **Given** any user, **When** they send `/whoami`, **Then** they see their Telegram id, whether they are linked and as whom, and the session key (the identifier of the session in that chat), and never an API key.
6. **Given** an unlinked user who taps a settings button, **When** the tap arrives, **Then** an alert says to link the account first.
7. **Given** a message already waiting for its turn, **When** the user's link is removed before the turn starts, **Then** the bot sends the linking reply instead of calling the endpoint.

---

### User Story 4 - Operator removes a link (Priority: P2)

The operator removes a link by Telegram id or by username, and the user's sessions are removed with it.

**Why this priority**: Access has to be revocable, but it is used far less than linking.

**Independent Test**: Link a user, give them a session, unlink by username, and confirm the link and every session of that user are gone and their next message gets the linking reply.

**Acceptance Scenarios**:

1. **Given** a linked user, **When** the operator unlinks by Telegram id, **Then** the link and all sessions of that user are removed and the command reports "removed 1 link(s)".
2. **Given** several links with the same username, **When** the operator unlinks by that username, **Then** all of them are removed.
3. **Given** no matching link, **When** the operator unlinks, **Then** the command reports "removed 0 link(s)" and fails.
4. **Given** neither an id nor a username, **When** the operator unlinks, **Then** the command fails with "give --telegram-id or --username".
5. **Given** the operator lists links, **When** the list is shown, **Then** it has the Telegram id, username and name of each, and never an API key.

### Edge Cases

- A code is made of 8 characters drawn from the letters A–Z without I and O and the digits 2–9. Two users cannot hold the same code; a clash is not handled (the creation fails).
- The username in a request is free text, taken as the words after `/start` joined by single spaces. It is not checked against the endpoint.
- Expired codes are not removed from storage; they are ignored by every rule above.
- Approving a code does not check that the key works.
- If the user cannot be told that they are linked (no bot token in the operator's environment, or Telegram refuses), the link is still made; the operator sees "linked, but the user could not be notified" only when sending was attempted and failed.
- The notice for unlinked group users is remembered in memory only; after a restart it can repeat sooner.
- A stored key that cannot be decrypted with the configured key makes that user's turns fail (see GAP-008).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST ignore, for every purpose except linking itself, any message from a user who has no link, and MUST NOT call the endpoint for them.
- **FR-002**: System MUST create a pairing code only for a `/start` sent in a private chat by an unlinked user who gave a username.
- **FR-003**: System MUST make a pairing code of 8 characters chosen uniformly from the letters A–Z without I and O and the digits 2–9, valid for 3,600 seconds from creation.
- **FR-004**: System MUST refuse a new code when the user's newest live code is less than 600 seconds old, and otherwise when the user already holds 3 live codes; the first rule is checked first.
- **FR-005**: System MUST reply to a refused code request with the reply for its reason and MUST leave stored codes unchanged.
- **FR-006**: System MUST store with each code the Telegram id, the username given, the user's full Telegram name, the chat id and the creation time.
- **FR-007**: System MUST let the operator list the codes younger than 3,600 seconds, and show one code in full or as one field (username, Telegram name or Telegram id).
- **FR-008**: System MUST match a code case-insensitively and ignore spaces and hyphens in it.
- **FR-009**: System MUST approve a code only with an API key read from the first line of standard input, trimmed, and MUST fail without changing anything when that line is empty.
- **FR-010**: System MUST, on approval, store the key encrypted with the configured key, record the username, name and Telegram id from the code, remove the code, and replace any earlier link of the same Telegram id.
- **FR-011**: System MUST, on approval, send the user "You are linked. Send me a message to begin." to the chat the code came from, and MUST NOT fail the approval when that message cannot be sent.
- **FR-012**: System MUST let the operator combine the steps: for a live code, issue an endpoint key for the code's username with the label `telegram`, and approve the code with it; the key MUST NOT be printed or written to a file by that command.
- **FR-013**: System MUST let the operator remove links by Telegram id or username, remove the sessions of every removed user, and report the number of links removed.
- **FR-014**: System MUST NOT show an API key in any list, reply or log.
- **FR-015**: System MUST answer `/start`, `/stop`, `/help` and `/whoami` for unlinked users, and MUST require a link for every other command and for the settings buttons.
- **FR-016**: System MUST tell an unlinked user in a private chat how to link on every message or gated command, and an unlinked user in a group at most once per 600 seconds per user.
- **FR-017**: System MUST re-check the link when a queued message's turn starts, and MUST answer with the linking reply instead of calling the endpoint when the link is gone.
- **FR-018**: System MUST answer `/whoami` with three lines: the Telegram id, `linked as <username>` or `not linked`, and the session key, and MUST NOT include an API key.

### Key Entities

- **Link**: ties one Telegram user to one API key of the endpoint, with the username and name recorded at approval.
- **Pairing code**: a request to be linked, naming the Telegram user, the username they claim and the chat to answer in.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A pairing code is exactly 8 characters long and never contains `0`, `O`, `1` or `I`.
- **SC-002**: A code stops working 3,600 seconds after it was created.
- **SC-003**: One user can create at most one code per 600 seconds and never holds more than 3 live codes.
- **SC-004**: No stored API key is readable in the database without the configured encryption key.
- **SC-005**: A message from an unlinked user is answered without any request to the endpoint.

## Assumptions

- The operator can reach the endpoint's key-issuing command and the bot's operator commands from the host that runs the stack.
- Telegram delivers the user's `/start` with the user's id and full name; both are taken as given.
- The configured encryption key stays the same for the life of the stored links.

## Known Defects and Gaps

### Gaps

- **GAP-007** (User Story 1): Pairing codes older than one hour are never deleted. Status: Scheduled for the next task.
- **GAP-008** (User Story 3): When a stored key cannot be decrypted with the configured key, every turn of that user fails and the user receives no reply. Status: Scheduled for the next task.
- **GAP-009** (User Story 2): If the operator's combined command issues an endpoint key and the approval then fails (for example the code expired in between), the key stays active. Status: Scheduled for the next task.
