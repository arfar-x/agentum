#!/usr/bin/env bash
# Creates the module's env file from its template and fills what can be generated.
# Idempotent: it never overwrites a value that is already set.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck disable=SC1091
source scripts/lib/env.sh

DIR=telegram-bot-gateway
ENV_FILE_PATH="${DIR}/.env.telegram-bot-gateway"

set_var() {  # set_var NAME VALUE -- replace the NAME= line, or append one
  local name="$1" value="$2" tmp line found=0
  tmp="$(mktemp)"
  while IFS= read -r line || [ -n "${line}" ]; do
    if [ "${found}" = 0 ] && [[ "${line}" =~ ^${name}= ]]; then
      printf '%s=%s\n' "${name}" "${value}"; found=1
    else
      printf '%s\n' "${line}"
    fi
  done < "${ENV_FILE_PATH}" > "${tmp}"
  [ "${found}" = 1 ] || printf '%s=%s\n' "${name}" "${value}" >> "${tmp}"
  cat "${tmp}" > "${ENV_FILE_PATH}"; rm -f "${tmp}"
}

if [ ! -f "${ENV_FILE_PATH}" ]; then
  cp "${DIR}/.env.example" "${ENV_FILE_PATH}"
  chmod 600 "${ENV_FILE_PATH}"
  echo "    created ${ENV_FILE_PATH}"
fi

if [ -z "$(env_get DB_PASSWORD "${ENV_FILE_PATH}")" ]; then
  pw="$(openssl rand -hex 24)"
  set_var DB_PASSWORD "${pw}"
  set_var DATABASE_URL "postgresql://telegram_bot:${pw}@gateway-db:5432/telegram_bot"
  echo "    generated DB_PASSWORD"
fi
if [ -z "$(env_get CREDS_KEY "${ENV_FILE_PATH}")" ]; then
  set_var CREDS_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
  echo "    generated CREDS_KEY"
fi
for name in BOT_TOKEN DEFAULT_MODEL; do
  [ -n "$(env_get "${name}" "${ENV_FILE_PATH}")" ] || { echo "    still needed: set ${name} in ${ENV_FILE_PATH}"; MISSING=1; }
done
[ -z "${MISSING:-}" ] || exit 3
