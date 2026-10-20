# Operator guide

## First run

1. Create a bot with @BotFather and copy its token.
2. `make telegram-setup` creates `telegram-bot-gateway/.env.telegram-bot-gateway` (generating `DB_PASSWORD` and
   `CREDS_KEY`) and the `telegram_bot` role and database on `gateway-db`.
3. Set `BOT_TOKEN` and `DEFAULT_MODEL` (an agent id or model name served by the endpoint) in that file.
4. `make telegram-up` starts the agent gateway and the bot. `make telegram-logs` follows the log.

`OPENAI_BASE_URL` defaults to `http://agent-gateway:8080/v1`. Point it at any other OpenAI-compatible endpoint
and the bot works the same, but `make telegram-pair` issues its keys from agent-gateway; for another endpoint
approve with a key you made yourself:

```bash
printf '%s\n' "$KEY" | docker compose exec -T telegram-bot-gateway python -m telegram_bot_gateway pair approve CODE
```

## Linking a user

1. The user opens the bot in a private chat and sends `/start <username>` (the name your user management
   knows them by, for example their email). They get a code valid for one hour.
2. `make telegram-codes` lists open codes with the username and Telegram name they gave. Check that it is who
   it says it is: **this check is the only thing that stops someone claiming another person's account.**
3. `make telegram-pair CODE=ABCD2345` issues a gateway key for that username and links it. The user is told.

Each user's gateway credentials for Jira and the like are still set by you
(`make gateway-creds-set USER=...`); the bot never handles them.

`make telegram-links` lists linked users. `make telegram-unlink TG_ID=123` (or `USER_NAME=alice@example.org`)
removes the link and the user's conversations. It does not revoke the gateway key; do that with
`make gateway-key-revoke KEY_ID=...`.

## What users can do

`/new`, `/stop`, `/model`, `/reasoning`, `/set <name> <value>`, `/unset <name>`, `/settings`, `/whoami`,
`/help`. Settings are per conversation and sent with every request; an endpoint that refuses a value (an agent
may lock some) says so and the message is shown unchanged.

## Switches

| Variable | Effect |
|---|---|
| `REACTIONS` | 👀 / 👍 / 👎 on the message being answered (default off; the typing indicator is enough). |
| `SHOW_PROGRESS` | Show the endpoint's reasoning as one silent message, edited in place and removed after the answer (default off). |
| `REQUEST_TIMEOUT` | Seconds before a request to the endpoint is abandoned. |
| `WEBHOOK_URL`, `WEBHOOK_SECRET` | Webhook instead of long polling; the secret is mandatory with the URL. The bot publishes no port, so put your own listener in front. |

## Backup and restore

The bot's data lives in the `telegram_bot` database inside `gateway_db_data`; `make backup` covers it and also
copies the env file. Restoring the database next to a different `CREDS_KEY` leaves every stored key unreadable:
everyone has to be linked again.

## When it looks wrong

- **No answer, log says another instance is polling:** a second bot process uses the same token; stop it.
- **A user gets "not linked":** their link was removed or never approved; check `make telegram-links`.
- **"The endpoint answered 401":** the user's gateway key was revoked; unlink and pair again.
- **Container unhealthy:** the bot writes a heartbeat file every 30 seconds; none means it is not polling.
  `make telegram-logs` shows why (a wrong token or an unreachable database are the usual causes).
