#!/usr/bin/env bash
# End-to-end check of the identity setup against a throwaway Keycloak
# (specs/003-identity-access/quickstart.md, steps 1 and 3 to 4, plus the Keycloak half of 7).
#
# What it does:
#   1. starts quay.io/keycloak/keycloak (start-dev) in a container, unless one already answers at --url;
#   2. applies integrations/keycloak/apply.sh (twice: the second run must change nothing), creating the
#      test people alice (product, leads), bob (engineering) and carol (disabled);
#   3. signs alice and bob in the way the Telegram bot does (OAuth 2.0 device grant, `openid
#      offline_access`), driving Keycloak's own login pages, and checks the tokens: audience, the
#      groups claim (leaf names), resource_access.agent-gateway.roles (roles that came through
#      groups), an offline refresh token (risk R1), refresh-token rotation;
#   4. checks the directory service account can read people and cannot write;
#   5. checks that a disabled person cannot sign in and that disabling alice makes her refresh fail;
#   6. if an agent gateway that accepts Keycloak tokens answers at GATEWAY_URL, checks which agents
#      alice and bob see; otherwise it says so and skips those steps.
#
# Nothing here touches a real Keycloak, model or deployment. Test passwords are random and live in a
# temporary directory that is removed on exit; no secret is printed.
#
# Usage: scripts/identity-e2e.sh [--url URL] [--keep] [--gateway-url URL]
#   --url URL          Keycloak to use (default http://localhost:8480). If nothing answers there and
#                      the URL is localhost, a container named $KC_CONTAINER is started.
#   --keep             leave the container running afterwards (for poking around)
#   --gateway-url URL  the agent gateway (default $GATEWAY_URL or http://127.0.0.1:8090)
#
# Needs: docker (to start Keycloak), curl, jq, openssl.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

KC_IMAGE="${KC_IMAGE:-quay.io/keycloak/keycloak:26.8.1}"
KC_CONTAINER="${KC_CONTAINER:-kc-e2e}"
KC_URL="http://localhost:8480"
GW_URL="${GATEWAY_URL:-http://127.0.0.1:8090}"
KEEP=0
REALM=agentflow
ADMIN_USER=admin
ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-$(openssl rand -hex 12)}"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) KC_URL="${2:?--url needs a value}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --gateway-url) GW_URL="${2:?--gateway-url needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
KC_URL="${KC_URL%/}"
OIDC="${KC_URL}/realms/${REALM}/protocol/openid-connect"

for tool in curl jq openssl; do command -v "${tool}" >/dev/null || { echo "${tool} is required" >&2; exit 2; }; done

TMP="$(mktemp -d)"; chmod 700 "${TMP}"
STARTED=0
cleanup() {
  rm -rf "${TMP}"
  if [ "${STARTED}" = 1 ] && [ "${KEEP}" = 0 ]; then docker rm -f "${KC_CONTAINER}" >/dev/null 2>&1; fi
}
trap cleanup EXIT

PASS=0 FAIL=0 SKIP=0
pass() { PASS=$((PASS + 1)); printf '  ok    %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$*"; }
skip() { SKIP=$((SKIP + 1)); printf '  skip  %s\n' "$*"; }
step() { printf '\n%s\n' "$*"; }
check() {  # check DESCRIPTION EXPECTED ACTUAL
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (expected $2, got $3)"; fi
}

# --- 1. Keycloak ------------------------------------------------------------------------------
step "1. Keycloak ${KC_IMAGE} at ${KC_URL}"
answers() { curl -sf -o /dev/null "${KC_URL}/realms/master"; }
if ! answers; then
  case "${KC_URL}" in
    http://localhost:*|http://127.0.0.1:*) ;;
    *) echo "nothing answers at ${KC_URL}" >&2; exit 2 ;;
  esac
  command -v docker >/dev/null || { echo "docker is required to start Keycloak" >&2; exit 2; }
  port="${KC_URL##*:}"
  docker rm -f "${KC_CONTAINER}" >/dev/null 2>&1
  docker run -d --name "${KC_CONTAINER}" -p "127.0.0.1:${port}:8080" \
    -e KC_BOOTSTRAP_ADMIN_USERNAME="${ADMIN_USER}" -e KC_BOOTSTRAP_ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
    "${KC_IMAGE}" start-dev >/dev/null || { echo "could not start ${KC_IMAGE}" >&2; exit 2; }
  STARTED=1
  for _ in $(seq 1 90); do answers && break; sleep 2; done
  answers || { echo "Keycloak did not come up" >&2; docker logs --tail 20 "${KC_CONTAINER}" >&2; exit 2; }
  pass "started container ${KC_CONTAINER}"
else
  ADMIN_USER="${KEYCLOAK_ADMIN_USER:?an existing Keycloak needs KEYCLOAK_ADMIN_USER and KEYCLOAK_ADMIN_PASSWORD in the environment}"
  ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:?set KEYCLOAK_ADMIN_PASSWORD}"
  pass "using the Keycloak that already answers"
fi

# --- 2. The realm -----------------------------------------------------------------------------
step "2. Apply the realm (twice)"
export KEYCLOAK_ADMIN_USER="${ADMIN_USER}" KEYCLOAK_ADMIN_PASSWORD="${ADMIN_PASSWORD}"
apply() {
  integrations/keycloak/apply.sh --url "${KC_URL}" --realm "${REALM}" --public-url http://localhost:8090 \
    --test-users "${TMP}/users" --write-secrets "${TMP}/gateway.env" > "${TMP}/apply.$1.log" 2>&1
}
if apply 1; then pass "first apply"; else fail "first apply"; sed 's/^/        /' "${TMP}/apply.1.log"; echo "cannot continue"; exit 1; fi
if apply 2; then
  if grep -q 'created' "${TMP}/apply.2.log"; then fail "second apply created something"; sed 's/^/        /' "${TMP}/apply.2.log"; else pass "second apply changed nothing"; fi
else
  fail "second apply"; sed 's/^/        /' "${TMP}/apply.2.log"
fi

user_pw() { grep "^$1=" "${TMP}/users" | cut -d= -f2-; }
claims() {  # claims FILE FIELD -- the claims of a JWT held in FILE's JSON field
  jq -r --arg f "$2" '.[$f]' "$1" | jq -R 'split(".")[1] | gsub("-"; "+") | gsub("_"; "/") | @base64d | fromjson'
}

# --- 3. Device sign-in, as the Telegram bot does it -------------------------------------------------
# Drives Keycloak's own pages with curl: the sign-in form, then the consent page offline_access brings.
device_login() {  # device_login USER OUTFILE -- 0 when the person approved and tokens were issued
  local user="$1" out="$2" jar="${TMP}/jar.$1" dev="${TMP}/dev.$1.json" page action code uri
  : > "${jar}"
  curl -sS -d client_id=telegram-bot -d 'scope=openid offline_access' "${OIDC}/auth/device" > "${dev}" || return 1
  uri="$(jq -r .verification_uri_complete "${dev}")"
  page="$(curl -sSL -c "${jar}" -b "${jar}" "${uri}")"
  action="$(grep -o 'id="kc-form-login"[^>]*action="[^"]*"' <<<"${page}" | sed 's/.*action="//; s/"$//; s/&amp;/\&/g')"
  [ -n "${action}" ] || return 1
  page="$(curl -sSL -c "${jar}" -b "${jar}" --data-urlencode "username=$1" --data-urlencode "password=$(user_pw "$1")" "${action}")"
  if grep -q 'login-actions/consent' <<<"${page}"; then
    code="$(grep -o 'name="code" value="[^"]*"' <<<"${page}" | sed 's/.*value="//; s/"$//')"
    action="$(grep -o '<form[^>]*login-actions/consent[^>]*>' <<<"${page}" | sed 's/.*action="//; s/".*//; s/&amp;/\&/g')"
    page="$(curl -sSL -c "${jar}" -b "${jar}" --data-urlencode "code=${code}" -d accept=Yes "${KC_URL}${action}")"
  fi
  grep -q 'Device Login Successful' <<<"${page}" || return 1
  curl -sS -d client_id=telegram-bot -d grant_type=urn:ietf:params:oauth:grant-type:device_code \
    -d "device_code=$(jq -r .device_code "${dev}")" "${OIDC}/token" > "${out}"
  jq -e '.access_token' "${out}" >/dev/null
}

step "3. Device sign-in (client telegram-bot, scope openid offline_access)"
# Pending until approved: the grant must say so, not fail.
curl -sS -d client_id=telegram-bot -d 'scope=openid offline_access' "${OIDC}/auth/device" > "${TMP}/pending.json"
pending="$(curl -sS -d client_id=telegram-bot -d grant_type=urn:ietf:params:oauth:grant-type:device_code \
  -d "device_code=$(jq -r .device_code "${TMP}/pending.json")" "${OIDC}/token" | jq -r .error)"
check "an unapproved device code is authorization_pending" authorization_pending "${pending}"

if device_login alice "${TMP}/alice.json"; then pass "alice approved the device code and got tokens"; else fail "alice's device sign-in"; fi
if device_login bob "${TMP}/bob.json"; then pass "bob approved the device code and got tokens"; else fail "bob's device sign-in"; fi

if [ -s "${TMP}/alice.json" ] && jq -e .access_token "${TMP}/alice.json" >/dev/null 2>&1; then
  a="$(claims "${TMP}/alice.json" access_token)"
  check "alice: issuer" "${KC_URL}/realms/${REALM}" "$(jq -r .iss <<<"${a}")"
  check "alice: audience names agent-gateway" true "$(jq '[.aud] | flatten | index("agent-gateway") != null' <<<"${a}")"
  check "alice: azp" telegram-bot "$(jq -r .azp <<<"${a}")"
  check "alice: preferred_username" alice "$(jq -r .preferred_username <<<"${a}")"
  check "alice: groups are leaf names" '["leads","product"]' "$(jq -c '.groups | sort' <<<"${a}")"
  check "alice: resource_access.agent-gateway.roles came through groups" '["agent-user","agent-writer"]' \
    "$(jq -c '.resource_access["agent-gateway"].roles | sort' <<<"${a}")"
  check "alice: sub present" true "$(jq '.sub | type == "string" and length > 0' <<<"${a}")"
  # R1: is the refresh token an offline one, with the 30-day idle lifetime?
  r="$(claims "${TMP}/alice.json" refresh_token)"
  check "R1: refresh token type (Offline = offline_access honoured through the device grant)" Offline "$(jq -r .typ <<<"${r}")"
  check "R1: refresh_expires_in is the offline idle timeout (30 days)" 2592000 "$(jq -r .refresh_expires_in "${TMP}/alice.json")"
  check "R1: granted scope includes offline_access" true "$(jq '.scope | split(" ") | index("offline_access") != null' "${TMP}/alice.json")"
fi
if [ -s "${TMP}/bob.json" ] && jq -e .access_token "${TMP}/bob.json" >/dev/null 2>&1; then
  b="$(claims "${TMP}/bob.json" access_token)"
  check "bob: groups" '["engineering"]' "$(jq -c '.groups | sort' <<<"${b}")"
  check "bob: roles" '["agent-user"]' "$(jq -c '.resource_access["agent-gateway"].roles | sort' <<<"${b}")"
fi

# Refresh-token rotation: the new token works, the already used one does not.
refresh() {  # refresh REFRESH_TOKEN OUTFILE
  curl -sS -d client_id=telegram-bot -d grant_type=refresh_token --data-urlencode "refresh_token=$1" "${OIDC}/token" > "$2"
}
if [ -s "${TMP}/alice.json" ]; then
  old="$(jq -r .refresh_token "${TMP}/alice.json")"
  refresh "${old}" "${TMP}/alice2.json"
  if jq -e .access_token "${TMP}/alice2.json" >/dev/null 2>&1; then pass "refresh grant returns a new access token"; else fail "refresh grant"; fi
  new="$(jq -r '.refresh_token // empty' "${TMP}/alice2.json")"
  if [ -n "${new}" ] && [ "${new}" != "${old}" ]; then pass "refresh token rotated"; else fail "refresh token was not rotated"; fi
  refresh "${old}" "${TMP}/alice-reuse.json"
  check "reusing the old refresh token is refused" invalid_grant "$(jq -r .error "${TMP}/alice-reuse.json")"
  # A reuse revokes the chain on some versions; take a clean token for the later steps.
  device_login alice "${TMP}/alice.json" || fail "alice's second device sign-in"
fi

# --- 4. The directory service account ---------------------------------------------------------------
step "4. Directory service account (read-only)"
dir_secret="$(grep '^DIRECTORY_CLIENT_SECRET=' "${TMP}/gateway.env" | cut -d= -f2-)"
portal_secret="$(grep '^PORTAL_CLIENT_SECRET=' "${TMP}/gateway.env" | cut -d= -f2-)"
check "apply wrote the directory client secret" true "$([ -n "${dir_secret}" ] && echo true || echo false)"
check "apply wrote the portal client secret" true "$([ -n "${portal_secret}" ] && echo true || echo false)"
curl -sS -d grant_type=client_credentials -d client_id=agent-gateway-directory --data-urlencode "client_secret=${dir_secret}" \
  "${OIDC}/token" > "${TMP}/dir.json"
dir_token="$(jq -r '.access_token // empty' "${TMP}/dir.json")"
if [ -n "${dir_token}" ]; then
  pass "directory client credentials grant"
  : > "${TMP}/dir.hdr"; chmod 600 "${TMP}/dir.hdr"; printf 'Authorization: Bearer %s\n' "${dir_token}" > "${TMP}/dir.hdr"
  adm="${KC_URL}/admin/realms/${REALM}"
  api() { curl -sS -o "${TMP}/api.out" -w '%{http_code}' -H "@${TMP}/dir.hdr" "$@"; }
  check "directory: list users" 200 "$(api "${adm}/users?max=100")"
  check "directory: users found" 3 "$(jq 'length' "${TMP}/api.out")"
  check "directory: find the gateway client" 200 "$(api "${adm}/clients?clientId=agent-gateway")"
  gw_uuid="$(jq -r '.[0].id' "${TMP}/api.out")"
  alice_id="$(jq -r .sub <<<"${a:-null}")"
  if [ -n "${alice_id}" ] && [ "${alice_id}" != null ]; then
    check "directory: alice's effective client roles" 200 "$(api "${adm}/users/${alice_id}/role-mappings/clients/${gw_uuid}/composite")"
    check "directory: effective roles include those inherited through groups" '["agent-user","agent-writer"]' \
      "$(jq -c '[.[].name] | sort' "${TMP}/api.out")"
    check "directory: alice's groups" 200 "$(api "${adm}/users/${alice_id}/groups")"
  fi
  check "directory: creating a group is refused" 403 \
    "$(curl -sS -o /dev/null -w '%{http_code}' -H "@${TMP}/dir.hdr" -H 'Content-Type: application/json' -d '{"name":"nope"}' "${adm}/groups")"
  check "directory: creating a user is refused" 403 \
    "$(curl -sS -o /dev/null -w '%{http_code}' -H "@${TMP}/dir.hdr" -H 'Content-Type: application/json' -d '{"username":"nope"}' "${adm}/users")"
else
  fail "directory client credentials grant"
fi
rm -f "${TMP}/dir.hdr"

# --- 5. Disabled people --------------------------------------------------------------------------------------
step "5. Disabled people"
if device_login carol "${TMP}/carol.json" 2>/dev/null; then fail "a disabled person signed in"; else pass "carol (disabled) cannot sign in"; fi

admin_header() {
  : > "${TMP}/adm.hdr"; chmod 600 "${TMP}/adm.hdr"
  printf 'Authorization: Bearer %s\n' "$(curl -sS -d grant_type=password -d client_id=admin-cli --data-urlencode "username=${ADMIN_USER}" \
    --data-urlencode "password=${ADMIN_PASSWORD}" "${KC_URL}/realms/master/protocol/openid-connect/token" | jq -r .access_token)" > "${TMP}/adm.hdr"
}
set_enabled() {  # set_enabled USER true|false
  admin_header
  local id
  id="$(curl -sS -H "@${TMP}/adm.hdr" "${KC_URL}/admin/realms/${REALM}/users?username=$1&exact=true" | jq -r '.[0].id')"
  curl -sS -o /dev/null -w '%{http_code}' -X PUT -H "@${TMP}/adm.hdr" -H 'Content-Type: application/json' \
    -d "{\"enabled\": $2}" "${KC_URL}/admin/realms/${REALM}/users/${id}"
}
if [ -s "${TMP}/alice.json" ]; then
  cur="$(jq -r .refresh_token "${TMP}/alice.json")"
  refresh "${cur}" "${TMP}/alice3.json"
  jq -e .access_token "${TMP}/alice3.json" >/dev/null 2>&1 && cur="$(jq -r .refresh_token "${TMP}/alice3.json")"
  check "disable alice" 204 "$(set_enabled alice false)"
  refresh "${cur}" "${TMP}/alice-disabled.json"
  check "a disabled person's refresh fails with invalid_grant" invalid_grant "$(jq -r .error "${TMP}/alice-disabled.json")"
  check "re-enable alice" 204 "$(set_enabled alice true)"
fi

# --- 6. The gateway (only if it is there and takes Keycloak tokens) ------------------------------------------------
step "6. Agent gateway at ${GW_URL}"
gw_status() {  # gw_status TOKEN PATH -- HTTP status, body in $TMP/gw.out
  curl -sS -o "${TMP}/gw.out" -w '%{http_code}' --max-time 10 -H "Authorization: Bearer $1" "${GW_URL}$2" 2>/dev/null || echo 000
}
GW_USABLE=0
if [ ! -s "${TMP}/alice.json" ]; then
  skip "no token to try"
else
  tok="$(jq -r .access_token "${TMP}/alice2.json" 2>/dev/null)"
  [ -n "${tok}" ] && [ "${tok}" != null ] || tok="$(jq -r .access_token "${TMP}/alice.json")"
  case "$(gw_status "${tok}" /v1/models)" in
    000) skip "no gateway answers at ${GW_URL} (start it with make gateway-up, or pass --gateway-url)" ;;
    200) GW_USABLE=1 ;;
    401) skip "the gateway answers but does not accept Keycloak tokens yet (identity units not merged, or OIDC_ISSUER does not match ${KC_URL}/realms/${REALM})" ;;
    *) skip "the gateway answered with an unexpected status" ;;
  esac
fi
if [ "${GW_USABLE}" = 1 ]; then
  if grep -q '"open-agent"\|"product-agent"\|"role-agent"' "${TMP}/gw.out"; then
    models() { jq -r '.data[].id' "${TMP}/gw.out" | sort | tr '\n' ' '; }
    gw_status "${tok}" /v1/models >/dev/null
    for m in open-agent product-agent role-agent; do
      check "gateway: alice sees ${m}" true "$(jq --arg m "${m}" '[.data[].id] | index($m) != null' "${TMP}/gw.out")"
    done
    btok="$(jq -r .access_token "${TMP}/bob.json")"
    gw_status "${btok}" /v1/models >/dev/null
    check "gateway: bob does not see product-agent" false "$(jq '[.data[].id] | index("product-agent") != null' "${TMP}/gw.out")"
    check "gateway: bob sees role-agent (by role)" true "$(jq '[.data[].id] | index("role-agent") != null' "${TMP}/gw.out")"
    check "gateway: bob asking for product-agent gets 404" 404 "$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${btok}" \
      -H 'Content-Type: application/json' -d '{"model":"product-agent","messages":[{"role":"user","content":"hi"}]}' "${GW_URL}/v1/chat/completions")"
  else
    skip "the gateway accepts the token but does not have the quickstart's three test agents (open-agent, product-agent, role-agent); see quickstart step 2"
  fi
fi

# --- Result -----------------------------------------------------------------------------------------------------------
printf '\n%s passed, %s failed, %s skipped\n' "${PASS}" "${FAIL}" "${SKIP}"
[ "${FAIL}" = 0 ]
