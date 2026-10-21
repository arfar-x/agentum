#!/usr/bin/env bash
# Links the Telegram user behind a pairing code: issues a gateway API key for the username they
# claimed, and hands it to the bot over standard input (it never touches the terminal or a file).
# The two modules stay apart; this script is the only place that knows both.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
CODE="${1:?usage: pair.sh CODE}"
BOT=(docker compose exec -T telegram-bot-gateway python -m telegram_bot_gateway)

echo "Linking: $("${BOT[@]}" pair show "${CODE}")"
USERNAME="$("${BOT[@]}" pair show "${CODE}" --field username)"
docker compose exec -T agent-gateway python -m agent_gateway key create "${USERNAME}" --label telegram \
  | head -1 | "${BOT[@]}" pair approve "${CODE}"
