#!/usr/bin/env bash
# Run the service-backed tests against throwaway Postgres and Redis containers.
set -euo pipefail
cd "$(dirname "$0")/.."
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
tag="agw-test-$$"
cleanup() { docker rm -f "$tag-pg" "$tag-redis" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$tag-pg" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=gw -p 127.0.0.1::5432 "$PG_IMAGE" >/dev/null
docker run -d --name "$tag-redis" -p 127.0.0.1::6379 "$REDIS_IMAGE" >/dev/null
pg_port=$(docker port "$tag-pg" 5432/tcp | head -1 | sed 's/.*://')
redis_port=$(docker port "$tag-redis" 6379/tcp | head -1 | sed 's/.*://')
for _ in $(seq 1 60); do
  docker exec "$tag-pg" pg_isready -U postgres -d gw >/dev/null 2>&1 && break; sleep 0.5
done
sleep 1
export GATEWAY_TEST_DATABASE_URL="postgresql://postgres:test@127.0.0.1:${pg_port}/gw"
export GATEWAY_TEST_REDIS_URL="redis://127.0.0.1:${redis_port}/0"
python -m pytest -m services "$@"
