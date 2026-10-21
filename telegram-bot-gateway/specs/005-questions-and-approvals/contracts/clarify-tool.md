# Contract: The `clarify` tool

Declared on every request (capability 002). It is the only tool the bot declares.

## Declaration

```json
{
  "type": "function",
  "function": {
    "name": "clarify",
    "description": "Ask the user a question and wait for the answer. Offer choices when there are a few clear ones.",
    "parameters": {
      "type": "object",
      "properties": {
        "questions": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "question": { "type": "string" },
              "choices": { "type": "array", "items": { "type": "string" } }
            },
            "required": ["question"]
          }
        }
      },
      "required": ["questions"]
    }
  }
}
```

## Arguments the bot accepts

| Shape | Question | Choices |
|---|---|---|
| `{"questions": [{"question": q, "choices": [...]}]}` (declared) | `question` of the first element | `choices` |
| `{"questions": [{"question": q, "options": [...]}]}` | `question` of the first element | `options`; each is a string or an object whose `label` is used |
| `{"question": q, "options": [...]}` or `{"text": q}` (flat) | `question` or `text` | `choices` or `options` |
| not valid JSON | the raw text, trimmed | none |

Only the first element of `questions` is shown.

## The answer

The answer is a message in the conversation:

```json
{"role": "tool", "tool_call_id": "<id of the clarify call>", "content": "<label, or the user's text>"}
```

The content is the label of the tapped button exactly, the owner's next message text, or `(no answer)`.

## Approval cards

A question whose choices are `Approve` and `Reject` (any letter case, in that order) is an approval. The endpoint this bot was built for asks for a write approval with that pair, with a call id starting `approve_`, and reads the answer text: a text containing approve (or yes) approves, one containing reject (or deny) rejects, and any other answer counts as a rejection.

## Buttons

| Button | Callback data |
|---|---|
| choice number *i* (from 0) | `a:<i>` |
| `✏️ Other` | `a:o` |
