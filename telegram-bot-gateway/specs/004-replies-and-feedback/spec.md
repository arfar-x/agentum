# Feature Specification: Replies and Feedback

**Created**: 2026-10-21

**Status**: Accepted

**Input**: Existing system behavior

Terms follow [`../glossary.md`](../glossary.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read answers that look right in Telegram (Priority: P1)

An answer arrives formatted for Telegram: bold, italic, code, links, lists and quotes display as intended, long answers are split into numbered parts, and nothing is lost when Telegram rejects the formatting.

**Why this priority**: A message that fails to send, or shows stray symbols, makes the bot look broken.

**Independent Test**: Send answers containing each construct, a very long answer and a message Telegram refuses to format; check the result of each.

**Acceptance Scenarios**:

1. **Given** an answer with `**bold**`, `*italic*`, `~~strike~~`, `` `code` `` and a fenced block, **When** it is sent, **Then** it shows as bold, italic, strikethrough, inline code and a code block, and the characters inside code are not changed.
2. **Given** an answer with a heading line, **When** it is sent, **Then** the heading shows as a bold line.
3. **Given** a bullet list, **When** it is sent, **Then** each bullet shows as `•` followed by the text.
4. **Given** a link `[text](url)`, **When** it is sent, **Then** it shows as a link, including a url that holds one level of parentheses.
5. **Given** a table of at most 5 columns and 12 rows, **When** it is sent, **Then** each row shows as a bullet with its first cell in bold and the other cells as `<heading>: <value>`; a larger table shows as a code block.
6. **Given** an answer longer than Telegram allows, **When** it is sent, **Then** it arrives as several messages of at most 4,096 units each, ending with `(1/3)`, `(2/3)`, `(3/3)`, and a code block cut in the middle is closed and reopened.
7. **Given** Telegram rejects the formatting of a message, **When** that happens, **Then** the same text is sent once as plain text with the markers removed.
8. **Given** an answer that is empty, **When** the turn ends, **Then** no message is sent.

---

### User Story 2 - See that the bot is working (Priority: P1)

While a turn runs, the chat shows that the bot is typing.

**Why this priority**: Turns can take a minute; silence looks like failure.

**Independent Test**: Start a slow turn and see the typing indicator in the chat until the answer arrives.

**Acceptance Scenarios**:

1. **Given** a running turn, **When** it has run, **Then** the bot sends the typing action every 2 seconds, into the topic when there is one, until the turn ends.
2. **Given** a failure of the typing action, **When** it happens, **Then** the turn is not affected.

---

### User Story 3 - A reaction only when it tells something (Priority: P2)

The bot reacts with 👀 to a message only when that tells the user something: the message is waiting behind a running turn, or its turn is taking long. The reaction is removed when the message has been dealt with.

**Why this priority**: A reaction on every message is noise.

**Independent Test**: Send a message that is answered quickly (no reaction); send one that takes longer than 10 seconds (👀 appears, then disappears); send a message during a running turn (👀 appears, then disappears).

**Acceptance Scenarios**:

1. **Given** a turn that ends within 10 seconds, **When** the answer is sent, **Then** no reaction was set.
2. **Given** a turn that runs longer than 10 seconds, **When** the 10th second passes, **Then** the last message of the input gets 👀, and the reaction is removed when the turn ends, fails or is stopped.
3. **Given** a message that arrives while a turn runs, **When** it is queued, **Then** it gets 👀, removed when its turn ends or when `/stop` discards it.
4. **Given** reactions switched off, **When** any of the above happens, **Then** no reaction is set.
5. **Given** a failed reaction call, **When** it happens, **Then** it is ignored.

---

### User Story 4 - Watch the model think (Priority: P3)

When switched on, the chat shows the endpoint's reasoning as one quiet message that updates and disappears when the answer arrives.

**Why this priority**: Optional and off by default.

**Independent Test**: Switch progress on, run a turn whose stream carries reasoning, and see one silent message appear, change, and be deleted.

**Acceptance Scenarios**:

1. **Given** progress on, **When** reasoning text starts to arrive, **Then** one silent message shows `💭` and the last 600 characters of the reasoning, not as a reply.
2. **Given** more reasoning, **When** it arrives, **Then** the message is edited at most once every 2 seconds.
3. **Given** the turn ends, **When** the answer is sent, **Then** the progress message is deleted.
4. **Given** progress off (the default), **When** reasoning arrives, **Then** it is not shown.

---

### User Story 5 - Replies get through Telegram's quirks (Priority: P2)

Flood limits, a missing topic and a missing replied message do not stop an answer.

**Why this priority**: These happen in real groups.

**Independent Test**: Make Telegram answer with a flood wait and with "thread not found" and confirm the answer still arrives.

**Acceptance Scenarios**:

1. **Given** Telegram asks to wait, **When** a message is sent, **Then** the bot waits that long plus half a second and sends again, up to five attempts in all.
2. **Given** "thread not found", **When** a message is sent into a topic, **Then** it is sent again without the topic.
3. **Given** the message to reply to no longer exists, **When** the reply is sent, **Then** the answer is sent without a reply.
4. **Given** an answer split in parts, **When** it is sent, **Then** only the first part is a reply, and in a group topic no part is a reply.

### Edge Cases

- Reply notices from the endpoint (an unsupported tool, the round limit) are added in front of the answer, one per line, each starting `⚠️ `; with no answer text the notices alone are sent.
- A message that Telegram refuses for any other reason is not retried; the turn fails and the user sees nothing (see KD-001).
- Reaction marks are kept in memory; a restart during a turn leaves the 👀 on the message (see GAP-001).
- Edits of an unchanged message are ignored.
- Single-asterisk italics are not turned back to plain in the plain-text fallback.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST send the typing action every 2 seconds while a turn runs, into the topic when there is one, and MUST ignore a failure of that action.
- **FR-002**: System MUST, when reactions are on (default), set 👀 only on a message that is queued behind a running turn, or on the last message of a turn's input once the turn has run longer than the reaction delay (default 10 seconds).
- **FR-003**: System MUST remove those reactions when the turn ends, fails or is cancelled, and when `/stop` discards a queued or waiting message.
- **FR-004**: System MUST NOT set any reaction when reactions are off, when the message id is unknown, or for a turn that ends before the delay; a failed reaction call MUST be ignored.
- **FR-005**: System MUST, when progress is on (default off), show the endpoint's reasoning as one silent message, not a reply, holding `💭 ` and the last 600 characters, edited no more than once every 2 seconds, and delete it when the turn ends.
- **FR-006**: System MUST send an answer as messages of at most 4,096 UTF-16 units, split at the last blank line, line break or space past one third of the part, or at the limit when there is none; each part MUST leave 16 units for its suffix.
- **FR-007**: System MUST end each part, when there is more than one, with a line break and `(i/n)`.
- **FR-008**: System MUST close a code block at a cut and open it again in the next part.
- **FR-009**: System MUST split again, at half its length, any part whose formatted text is longer than the limit.
- **FR-010**: System MUST send answers in Telegram's MarkdownV2 form: fenced and inline code kept with only the backslash and the backtick escaped inside; `**x**` and `__x__` as bold; `~~x~~` as strikethrough; `*x*` and `_x_` as italic (not inside a word); headings as a bold line; lines starting `-`, `*` or `+` and a space as `•`; lines starting `>` as quotes; links with the backslash and the closing parenthesis escaped in the address; every other special character escaped.
- **FR-011**: System MUST show a table of up to 5 columns and 12 rows as bullets, each with its first cell in bold, followed by ` — ` and the other non-empty cells as `<heading>: <value>` joined by `; `, and any larger table as a code block.
- **FR-012**: System MUST resend a message as plain text, once, when Telegram rejects its formatting, with code markers and fences removed, links written as `text (url)`, and bold (`**` and `__`), strike and heading marks removed.
- **FR-013**: System MUST make only the first part of an answer a reply to the triggering message (the last message of the input), without failing when that message is gone, and MUST NOT make any part a reply in a group topic.
- **FR-014**: System MUST send into the topic of the session and, when Telegram says the topic is not found, send again without it.
- **FR-015**: System MUST, when Telegram asks to wait, wait that time plus 0.5 seconds and send again, with at most 5 attempts per message in all.
- **FR-016**: System MUST let any other refusal by Telegram, or the fifth failed attempt, end the turn as a failure.
- **FR-017**: System MUST send answers, notices and cards with a notification, and the progress message without one.
- **FR-018**: System MUST put each notice from the turn before the answer, one per line, each beginning `⚠️ `, and MUST send the notices alone when the answer has no text.
- **FR-019**: System MUST store the plain text of every message it sends, by chat and message id.
- **FR-020**: System MUST NOT send a message with no text.
- **FR-021**: System MUST apply the same formatting and fallbacks when it edits one of its messages, and ignore an edit that changes nothing.

### Key Entities

- **Answer**: the endpoint's final text, with the notices of the turn.
- **Part**: one message of a split answer, in MarkdownV2 and in plain text.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No sent message exceeds 4,096 UTF-16 units.
- **SC-002**: A turn ending within 10 seconds causes zero reaction calls.
- **SC-003**: A flood wait causes at most 5 attempts per message.
- **SC-004**: The progress message is edited at most once per 2 seconds.

## Assumptions

- Telegram accepts MarkdownV2 as documented and reports its formatting errors with the words "parse", "markdown" or "entities".
- Telegram allows the reaction 👀 on messages of a chat the bot is in.

## Known Defects and Gaps

The defects KD-001 (an unseen answer is saved, see capability 002) and the gap GAP-001 (the 👀 stays after a restart) also affect this capability.
