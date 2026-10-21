# Quickstart: Operations

End-to-end scenarios that confirm the module behaves as specified. They assume the stack is checked out, the gateway's database is running and a bot token exists.

## 1. First start

1. Run the preparation and database setup (`make telegram-setup`). Expect: the env file is created with mode 600, `DB_PASSWORD`, `DATABASE_URL` and `CREDS_KEY` are filled, and the step lists `BOT_TOKEN` and `DEFAULT_MODEL` as still needed.
2. Set `BOT_TOKEN` and `DEFAULT_MODEL` in the env file.
3. Start the bot (`make telegram-up`). Expect: within about 40 seconds the container is healthy and the log ends with `ready as @<username>`.
4. In Telegram, open the bot's command menu. Expect nine commands: new, stop, model, reasoning, set, unset, settings, whoami, help.

## 2. Configuration errors

1. Start the bot once with `OPENAI_BASE_URL` empty. Expect `error: missing required variable(s): OPENAI_BASE_URL` and exit status 2.
2. Start it with `WEBHOOK_URL` set and `WEBHOOK_SECRET` empty. Expect the secret message and exit status 2.

## 3. Link a user and chat

1. As an unlinked user in a private chat send `/start <username>`. Expect a code of 8 characters.
2. Run the operator listing (`make telegram-codes`). Expect the code with the username and name.
3. Approve it (`make telegram-pair CODE=<code>`). Expect `linked <username>` and a message "You are linked. Send me a message to begin." in the chat.
4. Send a message. Expect an answer from the endpoint.

## 4. Isolation

1. Connect to the database as the bot's role to a database other than `telegram_bot`. Expect a refusal ("permission denied for database").
2. Check the running service. Expect no published port.

## 5. Recovery

1. Stop the network to Telegram for a minute while the bot runs. Expect errors in the log and polling to resume afterwards.
2. Start a second process with the same token. Expect "Telegram reports another bot instance is polling this token; stop the other one." in the log.

## 6. Backup

1. Run the stack's backup. Expect the bot's env file in the backup folder, and the database inside the gateway's database volume archive.
