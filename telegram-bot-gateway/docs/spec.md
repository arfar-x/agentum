# Telegram bot gateway — software requirements specification

| | |
|---|---|
| **Spec ID** | SPEC-TG-001 |
| **Status** | Implemented; not yet run against the live Telegram API |
| **Version** | 0.2.0 |
| **Last updated** | 2026-10-20 |
| **Implements** | `telegram-bot-gateway/` module |
| **Related** | [`agent-gateway/docs/spec.md`](../../agent-gateway/docs/spec.md) |

Requirement keywords (**MUST**, **MUST NOT**, **SHOULD**, **MAY**) are used as defined in RFC 2119.

This specification is **executable**. Every requirement carries a stable id; `tests/test_spec_coverage.py` fails when a requirement marked *done* has no test claiming it, or when a test claims an id this document does not define. A test claims a requirement with a `# Covers: FR-LNK-01` line. Behavior changes here first, then in code. A requirement is `planned` until it is built and tested.

---

## 1. Purpose

A Telegram bot that is a client of any OpenAI-compatible Chat Completions endpoint. It is built to work with `agent-gateway` (an agent is a `model` name), but it depends on nothing gateway-specific: it works with any endpoint that speaks the same API. Its user experience follows the Hermes Agent Telegram adapter: DMs, groups, forum topics, replies, reactions, media and inline-button prompts. It adds forwarded-message handling, which Hermes lacks.

## 2. Glossary

| Term | Meaning |
|---|---|
| **Endpoint** | The OpenAI-compatible service the bot calls (`OPENAI_BASE_URL`). |
| **Link** | The record tying one Telegram user to one API key of the endpoint. |
| **Session** | One conversation: a message history plus settings, keyed by chat, topic and (in groups) user. |
| **Turn** | One request to the endpoint, including the tool-call round trips it needs. |
| **Trigger** | An incoming message the bot answers. |
| **Operator** | The person who runs the stack and approves links. |

## 3. Decisions of record

| Decision | Reason |
|---|---|
| Plain Chat Completions, no gateway-specific fields | Works with any endpoint; the Responses API drops images in agent-gateway. |
| One API key per Telegram user | In agent-gateway the key is the user: stored credentials, threads and approvals belong to it. A shared key would merge everyone. |
| No allowlist; a user must be linked | Users come from the stack's own user management, not from a bot-side list. |
| Operator approves every link | The approval is what stops a user claiming someone else's account. |
| State in its own database on the existing `gateway-db` instance | One Postgres to back up; the bot gets its own role and database, nothing else. |
| Queue, never interrupt | A gateway turn may run write tools; aborting mid-turn can hide a side effect. |
| No live-edit streaming | Costs an edit loop and a rate budget; the typing indicator covers the wait. |

## 4. Acceptance scenarios

| id | Scenario | Status |
|---|---|---|
| AS-01 | **Given** a linked user, **when** they DM the bot, **then** they get an answer from the endpoint under their own key. | done |
| AS-02 | **Given** an unlinked user, **when** they DM the bot, **then** they get a pairing code and nothing reaches the endpoint. | done |
| AS-03 | **Given** a group, **when** a member replies to the bot, **then** the bot answers in the same topic. | done |
| AS-04 | **Given** a write the endpoint asks to approve, **when** the owner taps Reject, **then** the endpoint receives the rejection and nothing is written. | done |
| AS-05 | **Given** a forwarded message, **when** it is sent to the bot, **then** the model sees where it came from. | done |

## 5. Requirements

### 5.1 Linking — `FR-LNK`

| id | Requirement | Status |
|---|---|---|
| FR-LNK-01 | A message from a user with no link MUST NOT reach the endpoint. | done |
| FR-LNK-02 | In a DM, `/start <username>` MUST create a pairing code (8 characters, no `0 O 1 I`, valid 1 hour) and show it to the user. | done |
| FR-LNK-03 | Pairing requests MUST be limited to one per user per 10 minutes and three pending per user. | done |
| FR-LNK-04 | The operator MUST be able to list pending codes, showing the claimed username and Telegram name, and approve one by supplying an API key on standard input. | done |
| FR-LNK-05 | An API key MUST be stored encrypted with a key from the environment and MUST NOT appear in a log, a reply or an error. | done |
| FR-LNK-06 | The operator MUST be able to unlink by Telegram id or username; the user's sessions MUST then stop working. | done |
| FR-LNK-07 | In a group, an unlinked user who triggers the bot MUST get one short "message me privately to link" reply, at most once per 10 minutes per user. | done |
| FR-LNK-08 | Linking MUST sit behind a port so another identity source can replace pairing without changing the rest. | done |
| FR-LNK-09 | The module MUST NOT import `agent-gateway`; the repository's root glue issues the key and passes it in. | done |

### 5.2 Sessions — `FR-SES`

| id | Requirement | Status |
|---|---|---|
| FR-SES-01 | A session key MUST be `chat[:thread]` in a DM and `chat[:thread]:user` in a group. | done |
| FR-SES-02 | Message history MUST be stored in the bot's own database and replayed to the endpoint exactly, including assistant `tool_calls`. | done |
| FR-SES-03 | A session MUST NOT expire; `/new` MUST start an empty one. | done |
| FR-SES-04 | The bot MUST store, per session, the model and the generation parameters set by the user, and send them on every request. | done |
| FR-SES-05 | The bot MUST store the text of messages it sent and received, by Telegram message id, so a reply can quote them. | done |
| FR-SES-06 | Replaying history MUST keep an assistant message byte-for-byte as returned; images in earlier turns MAY be replaced by a text placeholder. | done |

### 5.3 Incoming messages — `FR-INB`

| id | Requirement | Status |
|---|---|---|
| FR-INB-01 | In a DM every message from a linked user MUST be a trigger. | done |
| FR-INB-02 | In a group, a message MUST be a trigger only when it @mentions the bot, replies to a message of the bot, or is a `/command@bot`. | done |
| FR-INB-03 | In a group, a message that @mentions other bots but not this one MUST be ignored. | done |
| FR-INB-04 | Messages from other bots MUST be ignored, and a bot MUST NOT answer itself. | done |
| FR-INB-05 | A reply MUST be prefixed `[Replying to: "<text>"]`, using the user's own quote if they made one, else the text or caption of the replied message; media of the replied message MUST be attached. | done |
| FR-INB-06 | A forwarded message MUST be prefixed `[Forwarded from <name or title>, <date>]`, built from `forward_origin`, with a neutral name when the origin is hidden. | done |
| FR-INB-07 | `message_thread_id` MUST be kept only for real topic messages; the General topic MUST be thread `1`; a reply's thread id outside a topic MUST be dropped. | done |
| FR-INB-08 | A photo, an image document or an album MUST be sent as `image_url` data URLs; an album and photos sent within 0.8 s MUST become one message. | done |
| FR-INB-09 | A text document up to 100 KB MUST be inlined as `[Content of <name>]:`; any other document MUST become a text placeholder with its name and size. | done |
| FR-INB-10 | A sticker MUST become a text placeholder with its emoji; a location or venue MUST become text with coordinates. | done |
| FR-INB-11 | A caption MUST be treated as the message text. | done |
| FR-INB-12 | A voice note or audio file MUST get a short reply saying it is not supported, and MUST NOT reach the endpoint. | done |
| FR-INB-13 | Text messages arriving while the session is idle MUST be merged after a short quiet period; a chunk near 4,000 characters MUST wait longer for the next part of a split message. | done |
| FR-INB-14 | A media download failure MUST be reported to the user. | done |

### 5.4 Turns and busy sessions — `FR-TRN`

| id | Requirement | Status |
|---|---|---|
| FR-TRN-01 | A turn MUST call `POST /v1/chat/completions` with the stored history and the session's model and parameters. | done |
| FR-TRN-02 | Tool-call round trips MUST be resolved by the bot only for its own `clarify` tool; any other tool call from the endpoint MUST be reported to the user as unsupported. | done |
| FR-TRN-03 | Messages arriving during a turn MUST be queued and merged into the next turn of the same session. | done |
| FR-TRN-04 | `/stop` MUST cancel the running request, discard the queue, and MUST NOT leave an unanswered tool call in the stored history. | done |
| FR-TRN-05 | Different sessions MUST run concurrently; turns of one session MUST run in order. | done |
| FR-TRN-06 | An endpoint error MUST be shown to the user with its message, and the history MUST stay as it was before the turn. | done |
| FR-TRN-07 | A request MUST NOT outlive a configurable timeout (default 10 minutes). | done |
| FR-TRN-08 | Every request MUST begin with a system message telling the model it is talking in Telegram: keep answers short unless asked, use only Telegram-friendly Markdown and a few emojis, reply in the user's language, and shorter still in a group. The message MUST NOT be stored in the history. | done |

### 5.5 Outgoing messages — `FR-OUT`

| id | Requirement | Status |
|---|---|---|
| FR-OUT-01 | While a turn runs the bot MUST send a `typing` action every 2 seconds. | done |
| FR-OUT-02 | When reactions are enabled (default off), the trigger MUST get 👀 at start, 👍 on success and 👎 on failure, and the reaction MUST be cleared on `/stop`. A failure to react MUST be ignored. | done |
| FR-OUT-03 | Markdown MUST be converted to MarkdownV2 following Hermes's rules: code protected, headings as bold, tables as bullets or code, all other special characters escaped. | done |
| FR-OUT-04 | When Telegram rejects the formatting, the same text MUST be re-sent as plain text. | done |
| FR-OUT-05 | A message over 4,096 UTF-16 units MUST be split, each part suffixed `(i/n)`. | done |
| FR-OUT-06 | Only the first part MUST be a reply to the trigger; in a group forum topic no part is a reply. | done |
| FR-OUT-07 | Sending MUST pass `message_thread_id`, and on "thread not found" MUST retry without it. | done |
| FR-OUT-08 | Final answers and prompts MUST notify; status messages MUST be silent. | done |
| FR-OUT-09 | When progress display is enabled (default off), `reasoning_content` of the endpoint MUST appear as one silent message edited in place and removed after the answer. | done |
| FR-OUT-10 | Telegram flood control MUST be honored by waiting the `retry_after` it states. | done |
| FR-OUT-11 | An empty answer MUST send nothing and MUST NOT count as a failure. | done |

### 5.6 Questions and approvals — `FR-ASK`

| id | Requirement | Status |
|---|---|---|
| FR-ASK-01 | Every request MUST declare one function tool, `clarify`, taking `questions`, a list of `{question, choices}`; the first question is shown. | done |
| FR-ASK-02 | A call to `clarify` MUST be shown as a message with one inline button per option and a button for a free-text answer. | done |
| FR-ASK-03 | A tap MUST be sent to the endpoint as the tool message answering that call; a free-text answer MUST be the user's next message. | done |
| FR-ASK-04 | An approval card (options Approve and Reject) MUST be rendered like any other `clarify` call. | done |
| FR-ASK-05 | Only the user the session belongs to MUST be able to answer; a tap by anyone else MUST be refused with an alert. | done |
| FR-ASK-06 | After an answer the card MUST be edited to show the outcome and the buttons removed. | done |
| FR-ASK-07 | A pending question MUST survive a restart of the bot; one older than 24 hours MUST be shown as expired and cancelled. | done |

### 5.7 Commands and settings — `FR-CMD`

| id | Requirement | Status |
|---|---|---|
| FR-CMD-01 | The bot MUST support `/start`, `/new`, `/stop`, `/whoami`, `/help`, and MUST register its command menu with Telegram. | done |
| FR-CMD-02 | `/model` MUST show the endpoint's `GET /v1/models` as buttons; `/model <id>` MUST set it without the picker. | done |
| FR-CMD-03 | `/reasoning` MUST set `reasoning_effort` with buttons none, minimal, low, medium, high and default. | done |
| FR-CMD-04 | `/set <param> <value>` and `/unset <param>` MUST change one of `temperature`, `top_p`, `max_tokens`, `seed`, `verbosity`, `presence_penalty`, `frequency_penalty`, `stop`. | done |
| FR-CMD-05 | `/settings` MUST show the model and every parameter in effect, with a reset button. | done |
| FR-CMD-06 | Values MUST be checked for type and range before storing; a refusal by the endpoint MUST be shown unchanged. | done |
| FR-CMD-07 | In a group a command MUST act on the sender's own session. | done |
| FR-CMD-08 | `/whoami` MUST show the Telegram id, the linked username and the session key, and no secret. | done |

### 5.8 Transport and operation — `FR-OPS`

| id | Requirement | Status |
|---|---|---|
| FR-OPS-01 | The bot MUST receive updates by long polling; webhook mode MAY be enabled with `WEBHOOK_URL` and MUST then require `WEBHOOK_SECRET`. | done |
| FR-OPS-02 | Updates queued while the bot was offline MUST be dropped on a cold start. | done |
| FR-OPS-03 | A repeated `update_id` MUST be ignored. | done |
| FR-OPS-04 | The bot MUST apply its own SQL migrations at startup, in order, once. | done |
| FR-OPS-05 | The bot MUST publish no port and answer a health check for the container. | done |
| FR-OPS-06 | Secrets MUST come from the environment only and MUST NOT be logged. | done |
| FR-OPS-07 | Two bots polling one token MUST be reported plainly in the log. | done |

### 5.9 Configuration — `FR-CFG`

| id | Requirement | Status |
|---|---|---|
| FR-CFG-01 | Variables: `BOT_TOKEN`, `OPENAI_BASE_URL`, `OPENAI_API_KEY` (optional, for `/v1/models` before a user is linked), `DEFAULT_MODEL`, `DATABASE_URL`, `CREDS_KEY`, `REACTIONS`, `SHOW_PROGRESS`, `REQUEST_TIMEOUT`, `WEBHOOK_URL`, `WEBHOOK_SECRET`. | done |
| FR-CFG-02 | The module MUST NOT name a model, an organization or a user in a tracked file. | done |
| FR-CFG-03 | A missing required variable MUST stop the start with a message naming it. | done |

### 5.10 Architecture — `NFR-ARC`

| id | Requirement | Status |
|---|---|---|
| NFR-ARC-01 | The code MUST be hexagonal: `domain/` → `application/` → `adapters/`, and `tests/test_boundaries.py` MUST fail if `domain/` or `application/` import anything that does I/O. | done |
| NFR-ARC-02 | The module MUST carry its own compose file, env file, make targets, scripts and docs; the root MUST reach it with an `include:`, an `include` and a link. | done |
| NFR-ARC-03 | The service MUST join only its own network and `gateway-data`, MUST NOT join `backend`, and its database role MUST reach only its own database. | done |
| NFR-ARC-04 | The module MUST be movable to its own repository without code changes. | done |

## 6. Out of scope (v1)

| Item | Reason |
|---|---|
| Voice notes and speech-to-text | Decided out for v1 (FR-INB-12 refuses them). |
| Live-edit streaming | Costs an edit loop and a rate budget; typing is enough. |
| `/retry` | Rarely needed. |
| Detecting `approve <code>` in reply text | The declared `clarify` tool makes the gateway ask by tool call. |
| Interrupt and steer modes | Aborting a turn that runs write tools can hide a side effect. |
| Hermes `/topic` DM lobby, skill-bound topics, `/compress`, `/undo`, `/bg`, `/usage` | Not part of the experience we want to copy first. |
| Crash-redelivery ledger | Cost exceeds value for a chat client. |
| `sendRichMessage` | MarkdownV2 with a plain fallback is enough. |
| Observing unmentioned group messages | Privacy cost; the bot answers only when addressed. |
| Sticker vision | Placeholder is enough. |
| Keycloak linking | Planned as a second adapter of FR-LNK-08. |

## 7. Module layout

```
telegram-bot-gateway/
  AGENTS.md  README.md  pyproject.toml  Dockerfile
  compose.yaml          # service telegram-bot-gateway, profile `telegram`
  .env.example          # tracked; copied to .env.telegram-bot-gateway (gitignored)
  module.mk             # telegram-up/down/test/pair/unlink/links
  docs/spec.md  docs/OPERATOR_GUIDE.md
  migrations/001_init.sql
  src/telegram_bot_gateway/{domain,application,adapters,cli}
  tests/
```

Root glue: `make telegram-setup` (create role and database on `gateway-db`), `make telegram-pair CODE=…` (issues a gateway key and passes it to the bot's CLI over standard input), `make backup` including the module's env file.

## 8. Open points

| # | Question | Default |
|---|---|---|
| 1 | Is the bot's `OPENAI_API_KEY` needed before linking, only to list models for `/model`? | Use the user's own key; keep the variable optional. |
| 2 | Group sender identity: should the model see who wrote each message in a group topic shared by users? | No; sessions are per user. |
| 3 | `clarify` for endpoints that never call it: does declaring it change their behavior? | Declared always; check against agent-gateway and plain vLLM during build. |
