# The Telegram bot's make targets. The root Makefile includes this file; every recipe runs from
# the repository root, where `docker compose` lives.

.PHONY: telegram-setup telegram-up telegram-down telegram-logs telegram-test telegram-codes telegram-pair telegram-links telegram-unlink

telegram-setup: ## Create the telegram bot's env file, its database role and database (idempotent)
	integrations/telegram-bot/scripts/prepare.sh || test $$? -eq 3
	integrations/telegram-bot/scripts/setup-db.sh

telegram-up: ## Start the Telegram bot (needs the agent gateway; set BOT_TOKEN and DEFAULT_MODEL in integrations/telegram-bot/.env)
	integrations/telegram-bot/scripts/prepare.sh
	integrations/telegram-bot/scripts/setup-db.sh
	docker compose --profile gateway --profile telegram up -d --build agent-gateway telegram-bot-gateway

telegram-down: ## Stop the Telegram bot (its data stays in gateway-db)
	docker compose --profile telegram stop telegram-bot-gateway

telegram-logs: ## Follow the bot's log
	docker compose --profile telegram logs -f telegram-bot-gateway

telegram-test: ## Run the bot's suite, including the Postgres tests, in a throwaway container
	cd telegram-bot-gateway && . .venv/bin/activate && python -m pytest -q && scripts/test-services.sh -q

telegram-codes: ## Open pairing codes and who asked for them
	docker compose exec -T telegram-bot-gateway python -m telegram_bot_gateway pair list

telegram-pair: ## Link a user: `make telegram-pair CODE=ABCD2345` (issues a gateway key for the username they gave)
	@test -n "$(CODE)" || { echo "Usage: make telegram-pair CODE=<code from telegram-codes>" >&2; exit 1; }
	integrations/telegram-bot/scripts/pair.sh $(CODE)

telegram-links: ## Linked Telegram users
	docker compose exec -T telegram-bot-gateway python -m telegram_bot_gateway links

telegram-unlink: ## Unlink: `make telegram-unlink TG_ID=123` or `USER_NAME=alice@example.org` (the gateway key stays; revoke it with gateway-key-revoke)
	@test -n "$(TG_ID)$(USER_NAME)" || { echo "Usage: make telegram-unlink TG_ID=<telegram id> | USER_NAME=<username>" >&2; exit 1; }
	docker compose exec -T telegram-bot-gateway python -m telegram_bot_gateway unlink $(if $(TG_ID),--telegram-id $(TG_ID),) $(if $(USER_NAME),--username $(USER_NAME),)
