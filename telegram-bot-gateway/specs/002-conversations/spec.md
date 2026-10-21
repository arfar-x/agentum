# Feature Specification: Conversations

**Created**: 2026-10-21

**Status**: Accepted

**Input**: Existing system behavior

Terms follow [`../glossary.md`](../glossary.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get an answer from the endpoint (Priority: P1)

A linked user sends a message and receives the endpoint's answer. The session continues across messages: the endpoint is sent the whole history every time.

**Why this priority**: This is the product. Everything else shapes how a turn is started, shown or answered.

**Independent Test**: As a linked user, send two messages in a row and check that the second request to the endpoint carries the first message and its answer, the user's own key, the default model and a system note.

**Acceptance Scenarios**:

1. **Given** a linked user with no session, **When** they send a message, **Then** the endpoint receives a request with a system note first, then the message, with the default model, no settings, one declared tool (`clarify`), and the user's own API key; and the answer is sent back.
2. **Given** a session with history, **When** the user sends another message, **Then** the request carries the stored history exactly as it was returned, then the new message.
3. **Given** the user set a model and parameters, **When** a turn runs, **Then** the request uses that model and carries those parameters.
4. **Given** a message with images in an earlier turn, **When** a later turn runs, **Then** the earlier images are no longer sent and their text is kept.
5. **Given** the endpoint answers with no text, **When** the turn ends, **Then** nothing is sent to the chat.
6. **Given** the endpoint calls a tool other than `clarify`, **When** the turn runs, **Then** the call is answered "This client cannot run that tool.", the user is warned of it in the reply, and the turn continues.
7. **Given** the endpoint keeps calling tools, **When** the eighth round ends without an answer, **Then** the turn stops with the notice "Stopped: the endpoint kept calling tools." and no answer text.

---

### User Story 2 - Keep typing while it works (Priority: P1)

A user who sends more messages while a turn is running does not lose them and does not interrupt the turn. The messages wait and are answered together afterwards; `/stop` cancels instead.

**Why this priority**: A turn can run write tools on the endpoint's side; aborting one can hide a side effect, so waiting is the safe default.

**Independent Test**: Start a slow turn, send two more messages, release the turn, and confirm the endpoint receives the two messages as one message in a second request.

**Acceptance Scenarios**:

1. **Given** a running turn, **When** the user sends two messages, **Then** neither interrupts it, and after it ends one new turn runs whose message is the two joined by a line break.
2. **Given** a running turn and queued messages, **When** the user sends `/stop`, **Then** the request is cancelled, the queue is discarded, nothing is added to the history, and the bot replies "Stopped."
3. **Given** nothing running or waiting, **When** the user sends `/stop`, **Then** the bot replies "Nothing is running."
4. **Given** two different sessions, **When** both have messages, **Then** their turns run at the same time; within one session turns run one after another.

---

### User Story 3 - Start fresh, and come back later (Priority: P1)

A user clears the session with `/new`. Otherwise the session and its settings are kept, however long the gap, and survive a restart of the bot.

**Why this priority**: History is the only memory the endpoint has of the session.

**Independent Test**: Chat, restart the bot, chat again and see that the history is still sent; then send `/new` and see that the next request starts empty.

**Acceptance Scenarios**:

1. **Given** a session, **When** the user sends `/new`, **Then** any running turn and queued messages are cancelled, the session (history, settings, open question) is deleted, and the bot replies "Started a new conversation."
2. **Given** a session and a restart of the bot, **When** the user writes again, **Then** the stored history and settings are used.
3. **Given** a group, **When** two members chat in the same chat or topic, **Then** each has a separate session.

---

### User Story 4 - Choose the model and tune the request (Priority: P2)

A user picks the model or agent, the reasoning effort and generation parameters for their session, and sees what is set.

**Why this priority**: Useful and frequently used, but the defaults already work.

**Independent Test**: Set the model, reasoning and a parameter, read them back with `/settings`, and see the next request carry them.

**Acceptance Scenarios**:

1. **Given** a linked user, **When** they send `/model` with no argument, **Then** the bot shows the endpoint's models (at most 40) as buttons, and a tap sets the model.
2. **Given** a model name, **When** the user sends `/model <name>`, **Then** the model is set without checking that it exists, and the bot replies `model = <name>`.
3. **Given** a linked user, **When** they send `/reasoning` with no argument, **Then** the bot shows six buttons (none, minimal, low, medium, high, default); with an argument it sets the effort, and `default` removes it.
4. **Given** `/set <name> <value>` with a valid value, **When** it is sent, **Then** the setting is stored and the bot replies `<name> = <value>`.
5. **Given** a value outside its range or of the wrong type, **When** it is sent, **Then** the bot replies with the reason and stores nothing.
6. **Given** `/unset <name>`, **When** it is sent, **Then** the setting is removed and the bot replies `<name> reset to the endpoint's default`.
7. **Given** `/settings`, **When** it is sent, **Then** the bot lists the model and every setting in effect with a Reset button, which clears the model and all settings.
8. **Given** a setting changed while a turn is running, **When** the turn ends, **Then** the changed setting is kept.

---

### User Story 5 - See endpoint errors (Priority: P2)

When the endpoint refuses or fails, the user is told why, and the session is as it was.

**Why this priority**: Without it a failure looks like silence.

**Independent Test**: Make the endpoint answer 400 with a message; confirm the user sees the message and the next request does not contain the failed message.

**Acceptance Scenarios**:

1. **Given** the endpoint answers an error status, **When** the turn ends, **Then** the user sees "The endpoint answered <status>: <message>" (or "no details"), and the history is unchanged.
2. **Given** the endpoint sends no data for the request timeout, **When** the timeout passes, **Then** the user sees "The endpoint took too long to answer."
3. **Given** the endpoint cannot be reached, **When** the turn runs, **Then** the user sees "Could not reach the endpoint: <error name>".
4. **Given** an unexpected failure, **When** the turn runs, **Then** the user sees "Something went wrong while answering. Please try again." and the history is unchanged.

### Edge Cases

- The model name set with `/model <name>` is not checked; an unknown name fails at the endpoint with its own error.
- A turn needs the user's key. If the link is removed after the message was accepted, the turn answers with the linking reply (see capability 001).
- `/stop` also discards messages still waiting inside the short merge window.
- A group member's commands change only their own session.
- A message whose content has no text and no images is ignored before it reaches a session.
- A parameter the endpoint refuses (for example one an agent locks) is not checked by the bot; the endpoint's error is shown unchanged.
- `/unset reasoning_effort` is accepted, although `/set reasoning_effort` is refused (see KD-006).
- The model list is cut at 40 entries without saying so (see KD-007).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST send each turn to the endpoint as a streamed chat request carrying, in order: a system note, the session's stored history, and the new message; with the session's model (or the configured default) and the session's settings; with one declared tool, `clarify`; and with the user's own API key.
- **FR-002**: System MUST store, as the history, the user's messages as sent, the assistant's messages exactly as the endpoint returned them (text, and tool calls with their ids, names and arguments), and the answers given to tool calls.
- **FR-003**: System MUST, before each new message, remove the images from every earlier message, keep its text, and turn a message that has only text parts left back into plain text.
- **FR-004**: System MUST NOT store the system note in the history.
- **FR-005**: System MUST stop a turn after at most 8 calls to the endpoint, with the notice "Stopped: the endpoint kept calling tools." and an empty answer.
- **FR-006**: System MUST answer every tool call other than the first `clarify` call of a round with the text "This client cannot run that tool." and add the notice "The endpoint asked for an unsupported tool: <name>" to the reply.
- **FR-007**: System MUST end a turn with the text of the endpoint's last message when it has no tool calls; an answer with no text and no notice sends nothing.
- **FR-008**: System MUST save the session after a turn succeeds and before the reply is sent, and MUST take the model and settings from storage at that moment, so changes made during the turn are kept.
- **FR-009**: System MUST, when a turn fails or is cancelled, leave the stored history exactly as it was before the turn.
- **FR-010**: System MUST show an endpoint failure as one message: "The endpoint answered <status>: <message>" for an error status (with "no details" when the body has none), "The endpoint failed: <message>" for an error inside the stream, "The endpoint took too long to answer." on a timeout, "Could not reach the endpoint: <error name>" for any other transport failure.
- **FR-011**: System MUST show "Something went wrong while answering. Please try again." for any other failure of a turn, and write the failure to its log.
- **FR-012**: System MUST give up on a request when the endpoint sends no data for the configured request timeout (default 600 seconds) while connected or while sending, and on the connection after 10 seconds. The timeout restarts with every piece of data received.
- **FR-013**: System MUST merge the messages of one turn into one: texts joined by a line break, images added after the text.
- **FR-014**: System MUST queue messages that arrive while the session has a turn running, and run them as one turn after it.
- **FR-015**: System MUST run turns of different sessions at the same time and turns of one session in order.
- **FR-016**: System MUST on `/stop` cancel the running request, discard the queue and any messages still waiting to be merged, and reply "Stopped." when something was cancelled or discarded and "Nothing is running." otherwise.
- **FR-017**: System MUST on `/new` cancel as `/stop` does, delete the session, and reply "Started a new conversation."
- **FR-018**: System MUST keep a session without expiry, and keep history and settings across restarts.
- **FR-019**: System MUST give every session key a separate session: the chat, the topic when there is one, and the user when the chat is not private.
- **FR-020**: System MUST act on the sender's own session for every command, including in groups.
- **FR-021**: System MUST let a user set the model by name without checking it, and show the endpoint's models (at most 40) as buttons when no name is given.
- **FR-022**: System MUST let a user set the reasoning effort to one of none, minimal, low, medium or high, or remove it with `default`, and show those six as buttons when no value is given.
- **FR-023**: System MUST accept these settings, each in its range: temperature (0 to 2), top_p (0 to 1), max_tokens (whole number, at least 1), seed (any whole number), verbosity (low, medium, high), presence_penalty and frequency_penalty (-2 to 2), and stop (text, or a list of one to four texts written as JSON).
- **FR-024**: System MUST refuse an unknown name, a missing value, a value of the wrong type and a value out of range with a reply that names the problem, and store nothing.
- **FR-025**: System MUST send the stored settings with every request of the session.
- **FR-026**: System MUST show `/settings` as the model (marked default when not set) followed by one line per setting in effect, or "no other settings; the endpoint's defaults apply", with a Reset button that clears the model and all settings.
- **FR-027**: System MUST show every setting change as a reply of the form `<name> = <value>`, `reasoning = <level>`, `model = <name>`, or "<name> reset to the endpoint's default".
- **FR-028**: System MUST answer `/help` with the help text and every command with the replies listed in `contracts/commands.md`.

### Key Entities

- **Session**: the history, the model and settings, and at most one open question, for one session key and one user.
- **Message text**: the text of a message in a chat, kept by chat and message id so a reply can quote it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A turn makes at most 8 requests to the endpoint.
- **SC-002**: A request with no data arriving is abandoned after 600 seconds by default; a connection after 10 seconds.
- **SC-003**: A failed or cancelled turn changes the stored history by exactly zero messages.
- **SC-004**: `/model` shows at most 40 models.
- **SC-005**: Messages sent during a turn cause zero extra requests until that turn ends, and exactly one request for all of them afterwards.

## Assumptions

- The endpoint accepts a system message, streams Server-Sent Events in the Chat Completions shape, and accepts the settings listed in FR-023 or returns an error for them.
- Telegram delivers a message to one session only.

## Known Defects and Gaps

### Defects

- **KD-001** (FR-008): The reply is saved to the history before it is sent. When sending fails with a network error, the user never sees the answer, and the next message continues as if they had. Expected: the history holds only what the user was shown, or the send is retried. Status: Scheduled for the next task.
- **KD-003** (FR-001): The history grows without limit; no part is dropped or summarised. A long session eventually exceeds the model's context and every turn fails until the user sends `/new`. Expected: the history is kept within the model's context. Status: Scheduled for the next task.
- **KD-008** (FR-012): The request timeout applies to each wait for data, not to the whole request, so an endpoint that keeps sending data slowly is never abandoned. The module's earlier spec states that a request must not outlive the timeout. Expected: a request ends once the timeout has passed since it started. Status: Scheduled for the next task.
- **KD-006** (FR-023): `/unset reasoning_effort` is accepted, but `/set reasoning_effort` is refused with the unknown-setting reply; the effort is set with `/reasoning` only. Expected: the same names work for set and unset. Status: Scheduled for the next task.
- **KD-007** (FR-021): `/model` cuts the list at 40 entries without saying so. Expected: the user is told when the list is longer. Status: Scheduled for the next task.

### Gaps

- **GAP-001** (User Story 2): A message accepted before a restart and not yet answered (merging, queued or running) is never answered, and the user is not told. Status: Scheduled for the next task.
- **GAP-003** (User Story 1): Stored message texts are never deleted. Status: Scheduled for the next task.
- **GAP-006** (User Story 2): Nothing limits how many requests one user can have running against the endpoint at once across chats. Status: Scheduled for the next task.
