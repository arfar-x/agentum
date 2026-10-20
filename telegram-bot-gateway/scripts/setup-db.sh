#!/usr/bin/env bash
# Creates the bot's role and database on the gateway's Postgres. Idempotent; it also re-applies
# the password, so changing DB_PASSWORD and rerunning keeps both sides in step. The role can
# connect to its own database only: CONNECT is revoked from PUBLIC on the others.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck disable=SC1091
source scripts/lib/env.sh

PW="$(env_get DB_PASSWORD telegram-bot-gateway/.env.telegram-bot-gateway)"
[ -n "${PW}" ] || { echo "DB_PASSWORD is empty; run scripts/prepare.sh first" >&2; exit 1; }

docker compose --profile gateway up -d gateway-db >/dev/null
for _ in $(seq 1 60); do
  docker compose exec -T gateway-db pg_isready -U gateway -d gateway >/dev/null 2>&1 && break
  sleep 1
done

docker compose exec -T gateway-db psql -U gateway -d gateway -v ON_ERROR_STOP=1 -v pw="${PW}" -q <<'SQL'
SELECT format('CREATE ROLE telegram_bot LOGIN PASSWORD %L', :'pw')
  WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'telegram_bot') \gexec
SELECT format('ALTER ROLE telegram_bot PASSWORD %L', :'pw') \gexec
SELECT 'CREATE DATABASE telegram_bot OWNER telegram_bot'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'telegram_bot') \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', datname) FROM pg_database WHERE datallowconn \gexec
GRANT CONNECT ON DATABASE telegram_bot TO telegram_bot;
SQL
echo "    telegram_bot database ready"
