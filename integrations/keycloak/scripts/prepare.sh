#!/usr/bin/env bash
# Creates integrations/keycloak/.env.keycloak from its template if it is missing. Idempotent: it
# never overwrites a value that is already set. With --dev it also generates a random admin password
# when none is set (for the throwaway Keycloak of `make idp-up`; a real server's admin password is
# yours to enter).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
# shellcheck disable=SC1091
source scripts/lib/env.sh

FILE="${KEYCLOAK_ENV_FILE:-integrations/keycloak/.env.keycloak}"

if [ ! -f "${FILE}" ]; then
  cp integrations/keycloak/.env.keycloak.example "${FILE}"
  chmod 600 "${FILE}"
  echo "    created ${FILE}"
fi

if [ "${1:-}" = "--dev" ] && [ -z "$(env_get KEYCLOAK_ADMIN_PASSWORD "${FILE}")" ]; then
  tmp="$(mktemp)"
  pw="$(openssl rand -hex 16)"
  while IFS= read -r line || [ -n "${line}" ]; do
    if [[ "${line}" =~ ^KEYCLOAK_ADMIN_PASSWORD= ]]; then printf 'KEYCLOAK_ADMIN_PASSWORD=%s\n' "${pw}"; else printf '%s\n' "${line}"; fi
  done < "${FILE}" > "${tmp}"
  cat "${tmp}" > "${FILE}"; rm -f "${tmp}"
  echo "    generated KEYCLOAK_ADMIN_PASSWORD in ${FILE}"
fi
