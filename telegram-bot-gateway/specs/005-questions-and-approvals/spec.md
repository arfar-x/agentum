# Feature Specification: Questions and Approvals

**Created**: 2026-10-21

**Status**: Accepted

**Input**: Existing system behavior

Terms follow [`../glossary.md`](../glossary.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Approve or reject an action (Priority: P1)

When the endpoint wants the user's permission before it does something (for example a write), the chat shows a card with Approve and Reject buttons. Only the user whose session it is can answer.

**Why this priority**: Approvals guard writes to other systems; they must be clear and cannot be answered by someone else.

**Independent Test**: Have the endpoint ask for approval; tap Reject as another member (refused), then as the owner (the endpoint receives "Reject").

**Acceptance Scenarios**:

1. **Given** the endpoint calls the `clarify` tool with the choices Approve and Reject, **When** the turn ends, **Then** the chat shows a card with exactly those two buttons and no other.
2. **Given** a card, **When** the owner taps a button, **Then** the endpoint receives the button's label as the answer to that call, the card is edited to the question followed by `✅ <label>` with its buttons removed, and the turn continues.
3. **Given** a card, **When** another person taps a button, **Then** an alert says "This question is not yours." and nothing else happens.

---

### User Story 2 - Answer a question with a button (Priority: P1)

The endpoint may ask a question with a few choices. Each choice is a button, with one more for an answer in the user's own words.

**Why this priority**: It is how an agent asks the user to choose without a long exchange.

**Independent Test**: Have the endpoint ask "Which one?" with two choices; tap one and see the endpoint receive it.

**Acceptance Scenarios**:

1. **Given** a question with choices, **When** the card is shown, **Then** it has one button per choice, in order, and a `✏️ Other` button.
2. **Given** a question with no choices, **When** the card is shown, **Then** it has only `✏️ Other`.
3. **Given** a card, **When** the owner taps `✏️ Other`, **Then** a message tells them to send the answer as a message, and the question stays open.

---

### User Story 3 - Answer in the user's own words (Priority: P2)

While a question is open, the owner's next message is the answer.

**Why this priority**: A choice list never covers everything.

**Independent Test**: With a question open, send a text message and see the endpoint receive it as the answer.

**Acceptance Scenarios**:

1. **Given** an open question, **When** the owner sends a message, **Then** its text is sent to the endpoint as the answer, and no separate user message is added.
2. **Given** an open question, **When** the owner sends a message with no text, **Then** the answer is "(no answer)".
3. **Given** an open question, **When** the owner sends `/new`, **Then** the session, with the question, is deleted.

---

### User Story 4 - Old and foreign cards are refused (Priority: P2)

A card that is no longer open, or that belongs to someone else, does nothing.

**Why this priority**: Stale buttons must not run work.

**Independent Test**: Tap a card after `/new`; confirm the alert and no request to the endpoint.

**Acceptance Scenarios**:

1. **Given** a card whose question is no longer open (answered, or the session deleted), **When** anyone taps it, **Then** an alert says "This question is no longer open." and nothing else happens.
2. **Given** a question older than 24 hours, **When** the owner next sends a message, **Then** the question, and the assistant turn that asked it, are dropped from the history, the card is edited to the question followed by `⌛ Expired`, and the message is handled as a new message.
3. **Given** a restart of the bot, **When** the owner answers an open question, **Then** it is still open.

### Edge Cases

- An unrecognised choice index in a button gives the alert "Unknown choice."
- A second `clarify` call in the same round is answered "This client cannot run that tool." (see capability 002); only the first becomes a card.
- A card tapped more than 24 hours after it was sent is still accepted by the tap, and then handled as a new message holding only the label (see KD-004).
- After `/new` the old card stays on screen (see GAP-005).
- Arguments that are not valid JSON become the question text, with no choices.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST show the first question of a `clarify` call as a card, taking the choices from `choices`, or `options` when there is no `choices`, each choice being its text or its `label`, and the question text from `question` or `text`, or "?" when there is none.
- **FR-002**: System MUST treat arguments that are not valid JSON as the question text with no choices, and an empty text as "?".
- **FR-003**: System MUST give a card one button per choice, in order, and a `✏️ Other` button unless the choices are exactly Approve then Reject in either letter case.
- **FR-004**: System MUST record the card's message id with the open question after sending it, so the card can be found by chat and message id.
- **FR-005**: System MUST accept a button tap only from the session's owner, send the label to the endpoint as the answer to the open call, and edit the card to `<question>` and `✅ <label>` without buttons.
- **FR-006**: System MUST refuse a tap by another person with the alert "This question is not yours.", a tap on a card with no open question with "This question is no longer open.", and an unknown choice with "Unknown choice."
- **FR-007**: System MUST answer a tap on `✏️ Other` with "Send your answer as a message." and keep the question open.
- **FR-008**: System MUST send the text of the owner's next message as the answer to an open question, and "(no answer)" when it has none.
- **FR-009**: System MUST deliver an answer to the endpoint as a tool message with the id of the open call and the label or text as its content, and MUST NOT add a user message for it.
- **FR-010**: System MUST drop a question 86,400 seconds after it was created, at the owner's next message, together with the assistant message that asked it and the tool messages after it, edit its card to `<question>` and `⌛ Expired`, and handle the message as new.
- **FR-011**: System MUST keep an open question across restarts, as part of the session.
- **FR-012**: System MUST allow at most one open question per session.

### Key Entities

- **Open question**: the call id, question, choices, chat, creation time and card message id, stored in the session.
- **Card**: the message that shows an open question.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An approval card has exactly two buttons.
- **SC-002**: A tap by anyone but the owner causes zero requests to the endpoint.
- **SC-003**: An open question older than 86,400 seconds is never sent to the endpoint as an answer when the owner writes.

## Assumptions

- The endpoint treats the text "Approve" as approval and "Reject" as rejection when it asked with those choices.
- The endpoint passes its question in the shape given in `contracts/clarify-tool.md`, or in the flat shape also accepted.

## Known Defects and Gaps

### Defects

- **KD-004** (FR-010): A card tapped more than 24 hours after it was sent is accepted at the tap, then handled as a brand-new message that holds only the button label, with no question context, because the stale question is dropped when the turn starts. Expected: the tap is refused as expired. Status: Scheduled for the next task.

### Gaps

- **GAP-005** (User Story 4): `/new` deletes the session but leaves an open card on screen; tapping it later says the question is no longer open. Status: Scheduled for the next task.
