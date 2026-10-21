# Contract: Operator commands for linking

Run as `python -m telegram_bot_gateway <command>` inside the bot's container. The stack wraps them in make targets.

| Command | Input | Output (standard output) | Exit |
|---|---|---|---|
| `pair list` | none | One line per code younger than 3,600 s: `<code>  <username padded to 24> <Telegram name> (telegram id <id>)`; or `no open codes` | 0 |
| `pair show <code> [--field username\|telegram_name\|telegram_user_id]` | code (case-insensitive) | `<username> / <Telegram name> / telegram id <id>`, or only the field | 0; 1 with `unknown or expired code` on standard error |
| `pair approve <code>` | API key on the first line of standard input | `linked <username> (telegram id <id>)` | 0; 1 with `unknown or expired code`; 2 with `expected the API key on standard input` |
| `links` | none | One line per link: `<telegram id padded to 14> <username padded to 24> <Telegram name>` | 0 |
| `unlink [--telegram-id N] [--username U]` | at least one | `removed <n> link(s)` | 0 when n > 0; 1 when n = 0; 2 with `give --telegram-id or --username` |

`pair approve` sends a confirmation message through the Telegram HTTP API using the bot token when one is present in the environment. When sending fails it prints `linked, but the user could not be notified` to standard error and still exits 0.

Configuration errors (a missing required variable) print `error: missing required variable(s): <names>` to standard error and exit 2. These commands need no bot token.

## Stack targets

| Target | Effect |
|---|---|
| `telegram-codes` | `pair list` |
| `telegram-pair CODE=<code>` | Shows the code, issues an endpoint key for the code's username with the label `telegram`, and pipes the first line of the key command's output into `pair approve`. Fails before issuing a key when the code is unknown. |
| `telegram-links` | `links` |
| `telegram-unlink TG_ID=<id>` or `USER_NAME=<username>` | `unlink` with the given option; fails with a usage line when neither is given |

## Bot messages (private chat)

| Situation | Text |
|---|---|
| Linked user sends `/start` | `You are linked as <username>. Send me a message to begin.` |
| `/start` in a group | `Message me privately to link your account first.` |
| `/start` without username, or any gated message | `You are not linked yet. Send /start <your username> here and I will give you a code for the operator.` |
| Code created | `Your code is <code>. Give it to the operator, who links you with make telegram-pair CODE=<code>. It is valid for one hour.` |
| Asked within 600 s | `You already asked recently; wait a few minutes or use the code I gave you.` |
| Three live codes | `You have too many open codes. Ask the operator to approve one of them.` |
| Gated action in a group | `Message me privately to link your account first.` |
| Settings button, unlinked | alert `Link your account first.` |
| Approval done | `You are linked. Send me a message to begin.` |
