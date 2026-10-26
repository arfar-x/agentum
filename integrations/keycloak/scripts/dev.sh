#!/usr/bin/env bash
# A local, throwaway Keycloak for development and tests (`make idp-up` / `make idp-down`). It is NOT
# for production: start-dev, plain HTTP, no volume (removing the container removes the data), bound
# to 127.0.0.1. It is not part of the compose file because the stack uses an existing Keycloak.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
# shellcheck disable=SC1091
source scripts/lib/env.sh

FILE=integrations/keycloak/.env.keycloak
NAME=agentflow-keycloak-dev
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

case "${1:-up}" in
  up)
    integrations/keycloak/scripts/prepare.sh --dev
    image="$(env_get KEYCLOAK_DEV_IMAGE "${FILE}")"; image="${image:-quay.io/keycloak/keycloak:26.8.1}"
    port="$(env_get KEYCLOAK_DEV_PORT "${FILE}")"; port="${port:-8480}"
    if [ -n "$(docker ps -q -f "name=^${NAME}$")" ]; then
      echo "    ${NAME} is already running on 127.0.0.1:${port}"
    else
      docker rm -f "${NAME}" >/dev/null 2>&1 || true
      # The bootstrap admin goes in through the environment of the docker client (-e NAME without a
      # value), so the password is not on any command line.
      KC_BOOTSTRAP_ADMIN_USERNAME="$(env_get KEYCLOAK_ADMIN_USER "${FILE}")" \
      KC_BOOTSTRAP_ADMIN_PASSWORD="$(env_get KEYCLOAK_ADMIN_PASSWORD "${FILE}")" \
        docker run -d --name "${NAME}" -p "127.0.0.1:${port}:8080" \
          -e KC_BOOTSTRAP_ADMIN_USERNAME -e KC_BOOTSTRAP_ADMIN_PASSWORD "${image}" start-dev >/dev/null
    fi
    printf '    waiting for Keycloak'
    for _ in $(seq 1 90); do
      curl -sf -o /dev/null "http://127.0.0.1:${port}/realms/master" && { echo " -- up at http://localhost:${port}"; exit 0; }
      printf '.'; sleep 2
    done
    echo; echo "Keycloak did not come up; see: docker logs ${NAME}" >&2; exit 1 ;;
  down)
    if docker rm -f "${NAME}" >/dev/null 2>&1; then echo "    removed ${NAME}"; else echo "    ${NAME} was not running"; fi ;;
  *) echo "usage: dev.sh [up|down]" >&2; exit 2 ;;
esac
