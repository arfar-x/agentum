# Contract: The endpoint

The bot is a client of any service that speaks the OpenAI Chat Completions API. It uses two routes under `OPENAI_BASE_URL`.

## `POST <base>/chat/completions`

Headers: `Authorization: Bearer <the user's API key>` (omitted when the key is empty), JSON body.

Body:

| Field | Value |
|---|---|
| `model` | the session's model, or `DEFAULT_MODEL` |
| `messages` | `[system note, ...stored history]` (the note is first and is never stored) |
| `stream` | `true` |
| `tools` | `[clarify]` (schema in `specs/005-questions-and-approvals/contracts/clarify-tool.md`) |
| each stored setting | `temperature`, `top_p`, `max_tokens`, `seed`, `verbosity`, `presence_penalty`, `frequency_penalty`, `stop`, `reasoning_effort`, as stored |

User message content is a string, or a list of parts: `{"type": "text", "text": ...}` and `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}`.

### What the bot reads from the stream

- Lines starting `data:`; the payload `[DONE]` ends the stream.
- `choices[].delta.content` is appended to the answer.
- `choices[].delta.reasoning_content` is appended to a separate text and reported to the progress display when it is on.
- `choices[].delta.tool_calls[]`: entries are grouped by `index` (default 0); the call id is the last non-empty `id` seen; the function name and arguments are the concatenation of the fragments.
- A payload with an `error` key ends the turn with an error.

The assembled assistant message is `{"role": "assistant", "content": <text or null>, "tool_calls": [...]}` (the key `tool_calls` only when there are some), and it is stored as returned.

### Errors

| Condition | Message shown to the user |
|---|---|
| Status 400 or above | `The endpoint answered <status>: <error.message, or the body text, or "no details">` |
| `error` payload in the stream | `The endpoint failed: <message>` |
| Timeout | `The endpoint took too long to answer.` |
| Other transport failure | `Could not reach the endpoint: <error class name>` |

Timeouts: `REQUEST_TIMEOUT` seconds (default 600) for each wait (reading, writing, acquiring a connection), so the clock restarts whenever data arrives; the connection 10 seconds.

## `GET <base>/models`

Same authorization. The bot reads `data[].id`. A status of 400 or above is shown as above.

## System note

The first message of every request, role `system`:

> You are talking with the user in Telegram, a chat app used on phones and desktops.
> - Keep answers short by default: a few brief lines or a short list. Give long or detailed answers only when the user asks for detail.
> - Format only with Telegram-friendly Markdown: **bold**, *italic*, `code`, fenced code blocks, [links](url), lists with "-", and "> " quotes. Do not use tables, headings or images; use a bold line instead of a heading.
> - Use a few fitting emojis to make answers easy to scan, for example at the start of a list item. Do not overdo them.
> - Do not mention which documents or sources an answer came from unless the user asks.
> - Reply in the language the user writes in.

In any chat that is not private, this line follows: "This is a group chat, so keep answers even shorter and answer only what was asked."
