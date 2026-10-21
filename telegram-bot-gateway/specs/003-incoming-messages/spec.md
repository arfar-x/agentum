# Feature Specification: Incoming Messages

**Created**: 2026-10-21

**Status**: Accepted

**Input**: Existing system behavior

Terms follow [`../glossary.md`](../glossary.md).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Talk to the bot in a private chat (Priority: P1)

A linked user writes in a private chat and every message is a trigger.

**Why this priority**: It is the simplest way to use the bot and the one every other behavior builds on.

**Independent Test**: Send a text message in a private chat as a linked user and see it become the input of a turn.

**Acceptance Scenarios**:

1. **Given** a linked user in a private chat, **When** they send text, **Then** the text becomes the input of a turn.
2. **Given** a message edited after it was sent, **When** the edit arrives, **Then** it is ignored.
3. **Given** the same update delivered twice, **When** the second arrives, **Then** it is ignored.
4. **Given** a message from a bot, **When** it arrives, **Then** it is ignored.

---

### User Story 2 - Be answered in a group only when addressed (Priority: P1)

In a group the bot stays quiet unless someone addresses it: mentions it, replies to one of its messages, or sends it a command.

**Why this priority**: A bot that answers everything in a group is unusable and expensive.

**Independent Test**: In a group, send plain chat (no answer), then a message mentioning the bot (answer), then a reply to the bot's message (answer).

**Acceptance Scenarios**:

1. **Given** a group, **When** a member writes without mentioning or replying to the bot, **Then** nothing happens.
2. **Given** a group, **When** a member's message contains `@<bot username>` (any letter case), **Then** it is a trigger, and the handle is removed from the text.
3. **Given** a group, **When** a member replies to a message the bot sent, **Then** it is a trigger.
4. **Given** a group, **When** a message mentions another bot and not this one, **Then** it is not a trigger, even when it replies to this bot.
5. **Given** a group, **When** a message mentions this bot and another bot, **Then** it is a trigger and the handle is not removed.
6. **Given** a group command addressed to another bot (`/cmd@otherbot`), **When** it arrives, **Then** this bot ignores it.

---

### User Story 3 - The model knows what a message replies to or forwards (Priority: P1)

When a user replies to a message, the model is told what was replied to. When a user forwards a message, the model is told where it came from.

**Why this priority**: Without context, "why?" or "summarise this" cannot be answered.

**Independent Test**: Reply to a message with "why?" and forward a channel post with "summarise"; check the text of each turn.

**Acceptance Scenarios**:

1. **Given** a reply, **When** the message is handled, **Then** the text starts with `[Replying to: "<quoted text>"]` and a blank line.
2. **Given** a reply where the user selected part of the replied message, **When** it is handled, **Then** the selected part is the quoted text.
3. **Given** a reply to a message with a caption or no text, **When** it is handled, **Then** the caption is quoted, and when there is none the stored text of that message is quoted, and when there is none no quote is added.
4. **Given** a forwarded message, **When** it is handled, **Then** the text starts with `[Forwarded from <name>, <date and time> UTC]` and a line break, with the name of the user, hidden sender, chat or channel it came from, or "an unknown sender".
5. **Given** a message that is both a reply and a forward, **When** it is handled, **Then** the reply line comes first.
6. **Given** a message in a forum topic, **When** it is not a reply, **Then** no reply line is added, although Telegram attaches the topic's first message to it.

---

### User Story 4 - Send photos, files and places (Priority: P2)

A user sends a photo, an image file, a text file, a document, a sticker or a location, and the model receives something useful.

**Why this priority**: It extends what can be asked, but plain text works without it.

**Independent Test**: Send a photo with a caption, a text file and a PDF; check that the model receives the image, the file's content and a note about the PDF.

**Acceptance Scenarios**:

1. **Given** a photo, **When** it is handled, **Then** its largest size is sent to the model as an image, with the caption as the text.
2. **Given** a document whose type is an image, **When** it is handled, **Then** it is sent as an image.
3. **Given** a text file of at most 100,000 bytes, **When** it is handled, **Then** the text carries `[Content of <name>]:` and the file's text.
4. **Given** any other document or a larger text file, **When** it is handled, **Then** the text carries a note with its name, size and type saying its content is not available.
5. **Given** a sticker, a location, a venue or a video, **When** it is handled, **Then** the text carries a note describing it.
6. **Given** a voice note, an audio file or a video note, **When** it triggers the bot, **Then** the bot replies "Voice notes are not supported yet; please type your message." and nothing is queued.
7. **Given** a file that cannot be downloaded, **When** it is handled, **Then** the bot replies "I could not download that file; please try sending it again." and the message is dropped.
8. **Given** a reply to a message that has an image or a text file, **When** it is handled, **Then** that message's image or file text is attached too.

---

### User Story 5 - Several messages become one (Priority: P2)

Messages and photos that arrive close together, including an album and a long text that the app split, are answered as one message.

**Why this priority**: Without it an album or a pasted long text would cause several turns.

**Independent Test**: Send three photos as an album and check that one turn carries all three.

**Acceptance Scenarios**:

1. **Given** photos of one album or any message with an image, **When** more arrive within 0.8 seconds of the last one, **Then** they are one input.
2. **Given** text messages, **When** each arrives before the previous wait ends, **Then** they are one input; the wait is 0.18 seconds up to 320 characters, 0.24 up to 1,024, 0.3 above, and 1.0 from 4,000 characters.

---

### User Story 6 - Use topics (Priority: P2)

In a forum group or a private chat with topics, each topic has its own session and answers go to the same topic.

**Why this priority**: Group users rely on topics; without them the answers land in the wrong place.

**Independent Test**: Write in two topics and see two separate sessions.

**Acceptance Scenarios**:

1. **Given** a message in a topic, **When** it is handled, **Then** the session key includes that topic.
2. **Given** a forum group's General topic, **When** a message arrives with no topic id, **Then** it is treated as topic 1.
3. **Given** a plain reply outside a forum, **When** it arrives with the replied message's id as its thread id, **Then** that id is not used as a topic.

### Edge Cases

- A voice note sent by an unlinked user is also refused with the voice reply, before the linking reply.
- A trigger whose content has no text and no image (for example an empty caption on an unsupported item) is ignored.
- A message that mentions a person whose username ends in "bot" is taken as addressed to another bot (see KD-005).
- Commands are not triggers: they are handled by their own handlers (capabilities 001 and 002).
- Only new messages and button taps are received; channel posts, edited messages and inline queries are not.
- A long text that the app split into several messages arrives as separate messages. A part of at least 4,000 characters is followed by a 1.0-second wait, so the next part joins it; the wait after each part depends on that part's own length.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST handle new messages, commands and button taps only, and MUST ignore a repeated update id among the last 4,096 seen.
- **FR-002**: System MUST treat every message from a non-bot sender in a private chat as a trigger.
- **FR-003**: System MUST treat a message in a group as a trigger only when it mentions this bot, replies to a message sent by this bot, or is a command for this bot, unless FR-004 applies.
- **FR-004**: System MUST NOT treat a group message as a trigger when it mentions another bot and does not mention this bot.
- **FR-005**: System MUST NOT treat a message sent by a bot as a trigger.
- **FR-006**: System MUST detect a mention of this bot as an `@username` equal to its username without regard to letter case, or a text mention of its user id, in the text or, when there is no text, in the caption.
- **FR-007**: System MUST detect a mention of another bot as a text mention of another bot's user id, or an `@name` ending in "bot" that is not this bot's username.
- **FR-008**: System MUST regard a message as a reply only when it replies to a message that is neither a forum topic's opening message nor the message whose id is the topic id.
- **FR-009**: System MUST refuse a triggering voice note, audio file or video note with "Voice notes are not supported yet; please type your message.", before any check of the link, and MUST NOT queue it.
- **FR-010**: System MUST use the message's text, or its caption when there is no text, and in a group MUST remove every `@<username>` of this bot (any letter case, whole word) and trim the result when the message mentions no other bot.
- **FR-011**: System MUST put `[Replying to: "<text>"]` and a blank line before the text of a reply, where the text is the user's selected quote, else the replied message's text or caption, else the stored text of that message, and MUST add nothing when none is available or the text is blank.
- **FR-012**: System MUST put `[Forwarded from <name>, <YYYY-MM-DD HH:MM> UTC]` and a line break after any reply line and before the text, where the name is the sending user's full name, a hidden sender's name, the sending chat's title or the channel's title, and the date is the time of the original message; with no name known the name is "an unknown sender".
- **FR-013**: System MUST send the largest size of a photo, and a document whose type is an image, to the model as an image.
- **FR-014**: System MUST inline a document as `[Content of <name>]:` followed by its text (decoded as UTF-8, bad bytes replaced) when its type is text or its name ends in one of .txt .md .csv .json .yaml .yml .log .py .js .ts .sql .xml .html .sh .toml .ini, and its size is at most 100,000 bytes, and the file name defaults to "file".
- **FR-015**: System MUST describe any other document as `[Document: <name>, <size or ?> bytes, <type or unknown type>; its content is not available]`.
- **FR-016**: System MUST describe a sticker as `[The user sent a sticker <emoji>]`, a venue as `[Venue: <title>, <address> (<latitude>, <longitude>) <map link>]`, a location as `[Location: <latitude>, <longitude> <map link>]`, and a video or animation as `[The user sent a video, which this bot cannot read]`.
- **FR-017**: System MUST attach the images, and the text notes of documents, of the message that was replied to.
- **FR-018**: System MUST reply "I could not download that file; please try sending it again." and drop the message when a file cannot be downloaded.
- **FR-019**: System MUST ignore a trigger that has neither text nor images after the steps above.
- **FR-020**: System MUST wait before starting a turn so that related messages join one input: 0.8 seconds when the message has an image or belongs to an album, otherwise 0.18 seconds for up to 320 characters of text, 0.24 up to 1,024, 0.3 above that and 1.0 from 4,000; each new message of the session restarts the wait with its own length.
- **FR-021**: System MUST use a topic's id as the topic for a message that belongs to a topic, topic 1 for a forum chat's message with no topic id, and no topic otherwise.
- **FR-022**: System MUST store the text built for each triggering message (with its reply and forward lines and document text) by chat and message id, so a later reply to it can quote it.
- **FR-023**: System MUST handle commands apart from triggers, and in a group MUST ignore commands addressed to another bot.

### Key Entities

- **Incoming message**: text or caption, media, reply and forward information, sender, chat and topic.
- **Trigger**: an incoming message the bot answers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A group message that neither mentions nor replies to the bot causes zero requests to the endpoint.
- **SC-002**: A photo album sent within 0.8 seconds between photos causes one turn.
- **SC-003**: A text file of 100,000 bytes or less reaches the model as text; one byte more does not.
- **SC-004**: The same update id delivered twice causes one turn.

## Assumptions

- Telegram marks forum topics, replies and quotes in the way the current Bot API describes.
- A file download of up to the Telegram limit succeeds or fails as a whole.

## Known Defects and Gaps

### Defects

- **KD-005** (FR-007): Any `@name` ending in "bot" that is not this bot counts as another bot. A person whose username ends in "bot" cannot be mentioned together with a request to this bot without silencing it (when this bot is not mentioned too). Expected: another bot is recognised as a bot. Status: Scheduled for the next task; the corrected rule needs a decision, because Telegram does not say whether a plain `@name` belongs to a bot.
