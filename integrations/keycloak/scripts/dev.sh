#!/usr/bin/env bash
# A local, throwaway Keycloak for development and tests (`make idp-up` / `make idp-down`). It is NOT
# for production: start-dev, plain HTTP, no volume (removing the container removes the data), bound
# to 127.0.0.1 unless KEYCLOAK_DEV_BIND says otherwise (0.0.0.0 exposes the admin console, with its generated password, to the network). It is not part of the compose file because the stack uses an existing Keycloak.
#
# The gateway and the Telegram bot run in containers, where `localhost` is the container itself, yet
# the issuer URL must be the same for them and for your browser. So the dev Keycloak answers to the
# name `keycloak-dev` on its own port: containers reach it by joining the stack's telegram-gateway
# network under that alias (done here; rerun `make idp-up` after the stack's networks exist), and your
# browser reaches it through one /etc/hosts line (`127.0.0.1 keycloak-dev`, printed below when missing).
# The issuer to use is then http://keycloak-dev:<port>/realms/agentflow.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
# shellcheck disable=SC1091
source scripts/lib/env.sh

FILE=integrations/keycloak/.env.keycloak
NAME=agentflow-keycloak-dev
ALIAS=keycloak-dev
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

case "${1:-up}" in
  up)
    integrations/keycloak/scripts/prepare.sh --dev
    image="$(env_get KEYCLOAK_DEV_IMAGE "${FILE}")"; image="${image:-quay.io/keycloak/keycloak:26.8.1}"
    port="$(env_get KEYCLOAK_DEV_PORT "${FILE}")"; port="${port:-8480}"
    bind="$(env_get KEYCLOAK_DEV_BIND "${FILE}")"; bind="${bind:-127.0.0.1}"
    if [ -n "$(docker ps -q -f "name=^${NAME}$")" ] && \
       [ "$(docker port "${NAME}" 2>/dev/null | head -1)" != "${port}/tcp -> ${bind}:${port}" ]; then
      echo "    ${NAME} is published differently than KEYCLOAK_DEV_PORT=${port} / KEYCLOAK_DEV_BIND=${bind}; recreating it (its data is lost)"
      docker rm -f "${NAME}" >/dev/null
    fi
    if [ -n "$(docker ps -q -f "name=^${NAME}$")" ]; then
      echo "    ${NAME} is already running on 127.0.0.1:${port}"
    else
      docker rm -f "${NAME}" >/dev/null 2>&1 || true
      # The bootstrap admin goes in through the environment of the docker client (-e NAME without a
      # value), so the password is not on any command line.
      KC_BOOTSTRAP_ADMIN_USERNAME="$(env_get KEYCLOAK_ADMIN_USER "${FILE}")" \
      KC_BOOTSTRAP_ADMIN_PASSWORD="$(env_get KEYCLOAK_ADMIN_PASSWORD "${FILE}")" \
        docker run -d --name "${NAME}" -p "${bind}:${port}:${port}" \
          -e KC_BOOTSTRAP_ADMIN_USERNAME -e KC_BOOTSTRAP_ADMIN_PASSWORD "${image}" start-dev --http-port="${port}" >/dev/null
    fi
    printf '    waiting for Keycloak'
    for _ in $(seq 1 90); do
      curl -sf -o /dev/null "http://127.0.0.1:${port}/realms/master" && break
      printf '.'; sleep 2
    done
    curl -sf -o /dev/null "http://127.0.0.1:${port}/realms/master" || { echo; echo "Keycloak did not come up; see: docker logs ${NAME}" >&2; exit 1; }
    echo " -- up at http://localhost:${port}"
    # Join the stack's network (if it exists yet) so the gateway and the bot reach it as ${ALIAS}.
    net="$(docker network ls --format '{{.Name}}' | grep -E '(^|_)telegram-gateway$' | head -1 || true)"
    if [ -n "${net}" ]; then
      docker network disconnect "${net}" "${NAME}" >/dev/null 2>&1 || true
      docker network connect --alias "${ALIAS}" "${net}" "${NAME}"
      echo "    containers on ${net} reach it as http://${ALIAS}:${port}"
    else
      echo "    the stack's telegram-gateway network does not exist yet; run make idp-up again after make gateway-up"
    fi
    getent hosts "${ALIAS}" >/dev/null || echo "    your browser needs:  echo '127.0.0.1 ${ALIAS}' | sudo tee -a /etc/hosts"
    echo "    issuer for the gateway and the bot: http://${ALIAS}:${port}/realms/agentflow"
    exit 0 ;;
  down)
    if docker rm -f "${NAME}" >/dev/null 2>&1; then echo "    removed ${NAME}"; else echo "    ${NAME} was not running"; fi ;;
  *) echo "usage: dev.sh [up|down]" >&2; exit 2 ;;
esac
