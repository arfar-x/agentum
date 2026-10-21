# Glossary

Terms used by every artifact under `specs/`. Each artifact uses the canonical term and none of the words listed under *Avoid* for that concept.

| Term | Definition | Code names | Avoid |
|---|---|---|---|
| **Linked user** | A Telegram user whose Telegram id has a link. Only linked users reach the endpoint. | `Link`, table `links` | allowed user, authorized user, member |
| **Link** | The record that ties one Telegram user to one API key of the endpoint, with the username the user gave and their Telegram name. The key is stored encrypted. | `Link`, `links.api_key_enc` | account, registration, subscription |
| **Pairing code** | A one-time code of 8 characters that an unknown user receives and gives to the operator, who approves it to create a link. | `PairingRequest`, table `pairing_codes` | invite code, token, PIN |
| **Operator** | The person who runs the stack, approves pairing codes and removes links. | CLI `pair`, `links`, `unlink` | admin, owner (an *owner* is the user a session or card belongs to) |
| **Endpoint** | The OpenAI-compatible Chat Completions service the bot calls, addressed by `OPENAI_BASE_URL`. An agent of the endpoint is addressed as a model name. | `OpenAIChat`, `OPENAI_BASE_URL` | backend, gateway (the gateway is one possible endpoint), LLM, model server |
| **Session** | One conversation: the message history sent to the endpoint, the user's settings, and any open question. A user has one session per session key. | `Session`, table `sessions` | conversation, thread (the gateway's *thread* is a different thing), chat (a Telegram chat holds many sessions) |
| **Session key** | The text that identifies a session: the chat id, then the topic id when there is one, then the user id when the chat is not private. | `session_key` | conversation id, thread id |
| **Turn** | One cycle of work for a session: the user's message goes to the endpoint, with the tool round trips that need, and it ends with an answer or a question. | `turn.begin`, `turn.run` | run, request, exchange |
| **Trigger** | An incoming message the bot answers. In a private chat every message is one; in a group a message is one when it mentions the bot, replies to the bot, or is a command for it. | `is_trigger`, `Incoming` | command (a command is one kind of message), mention (one way to trigger) |
| **Batch** | Messages that are merged into the input of one turn, either because they arrived within a short window or because they arrived while a turn was running. | `Job`, `_Bucket`, `merge` | album (an album is one source of a batch), burst |
| **Queue** | The messages held while a turn runs, merged into the next turn of the same session. | `_Run.queue` | backlog, buffer |
| **Setting** | A per-session choice sent with every request: the model, the reasoning effort, or a generation parameter. | `Session.model`, `Session.params` | option, preference, config (*configuration* means the environment variables of the module) |
| **System note** | The short instruction sent as the first message of every request that tells the model it is talking in Telegram. It is never stored in the history. | `telegram_context` | system prompt (the endpoint's agent has its own), preamble |
| **Card** | A message with inline buttons that asks the user a question, or asks them to approve or reject an action, in answer to a `clarify` tool call. | `clarify` tool, `card_markup`, `Pending` | prompt, dialog, poll |
| **Open question** | A question the endpoint asked that the user has not yet answered. The history ends with the assistant's tool call, and the next message from the owner answers it. | `Pending`, `Session.pending` | pending prompt |
| **Topic** | A Telegram forum thread inside a group, or a topic of a private chat, identified by a thread id. | `message_thread_id`, `thread_id` | thread (the gateway's *thread* is a different thing), channel |

## Naming conflicts found in the code and documents

- *Conversation*, *session* and *thread* were used for the same thing. *Session* is the term; *thread* names only a Telegram topic. The capability is named *Conversations* and the bot says "Started a new conversation." to users; those two are names and a reply, not terms.
- *Question*, *card* and *approval* overlap. An *approval* is a card whose only options are Approve and Reject; a *card* is the message; an *open question* is the state.
- *User* meant both the Telegram person and the endpoint account. The artifacts say *linked user* for the person and *link* for the tie to the key.
