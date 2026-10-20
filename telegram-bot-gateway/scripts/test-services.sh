#!/usr/bin/env bash
# Run the Postgres-backed tests against a throwaway container.
set -euo pipefail
cd "$(dirname "$0")/.."
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
tag="tgb-test-$$"
cleanup() { docker rm -f "$tag-pg" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$tag-pg" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=bot -p 127.0.0.1::5432 "$PG_IMAGE" >/dev/null
pg_port=$(docker port "$tag-pg" 5432/tcp | head -1 | sed 's/.*://')
for _ in $(seq 1 60); do
  docker exec "$tag-pg" pg_isready -U postgres -d bot >/dev/null 2>&1 && break; sleep 0.5
done
sleep 1
export BOT_TEST_DATABASE_URL="postgresql://postgres:test@127.0.0.1:${pg_port}/bot"
python -m pytest -m services "$@"
