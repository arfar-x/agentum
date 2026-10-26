#!/usr/bin/env bash
# Creates or updates the `agentflow` realm on a Keycloak server from
# integrations/keycloak/realm/agentflow-realm.json, through the admin REST API.
#
# Idempotent: it creates what is missing and brings the parts the stack depends on
# (realm settings, the two custom client scopes and their mappers, the clients and their
# scopes, the client scope mappings, the directory client's read-only roles) back to the
# template. It does NOT touch what an operator owns once it exists: groups, the roles on a
# group, users and the people in groups are created once and never changed or removed by a
# rerun. Nothing in other realms is touched.
#
# Secrets: the admin password is read from the environment or the env file (never a flag),
# request bodies and the bearer token go through mode-600 temp files (not the process
# list), and no secret is printed. The generated client secrets are written to
# --write-secrets FILE (default .env.agent-gateway, when that file exists).
#
# Usage:
#   integrations/keycloak/apply.sh [--url URL] [--realm NAME] [--env-file FILE]
#       [--public-url URL] [--write-secrets FILE | --no-write-secrets]
#       [--test-users FILE]
#
#   --test-users FILE   also create the throwaway people alice (product, leads), bob (engineering)
#                       and carol (disabled) for the end-to-end run; their random passwords are
#                       written to FILE (mode 600) as `name=password` lines. Never use it on a
#                       real deployment.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck disable=SC1091
source scripts/lib/env.sh

ENV_FILE_KC=integrations/keycloak/.env.keycloak
TEMPLATE=integrations/keycloak/realm/agentflow-realm.json
URL_ARG="" REALM_ARG="" PUBLIC_ARG="" SECRETS_FILE="" NO_SECRETS=0 TEST_USERS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL_ARG="${2:?--url needs a value}"; shift 2 ;;
    --realm) REALM_ARG="${2:?--realm needs a value}"; shift 2 ;;
    --env-file) ENV_FILE_KC="${2:?--env-file needs a value}"; shift 2 ;;
    --public-url) PUBLIC_ARG="${2:?--public-url needs a value}"; shift 2 ;;
    --write-secrets) SECRETS_FILE="${2:?--write-secrets needs a value}"; shift 2 ;;
    --no-write-secrets) NO_SECRETS=1; shift ;;
    --test-users) TEST_USERS="${2:?--test-users needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

pick() {  # pick ARG ENV_NAME DEFAULT -- the flag, else the environment, else the env file, else the default
  local v="$1"
  [ -n "${v}" ] || v="${!2:-}"
  [ -n "${v}" ] || v="$(env_get "$2" "${ENV_FILE_KC}")"
  printf '%s' "${v:-$3}"
}
KC_URL="$(pick "${URL_ARG}" KEYCLOAK_URL http://localhost:8480)"
KC_URL="${KC_URL%/}"
REALM="$(pick "${REALM_ARG}" KEYCLOAK_REALM agentflow)"
GATEWAY_PUBLIC_URL="$(pick "${PUBLIC_ARG}" GATEWAY_PUBLIC_URL http://localhost:8090)"
GATEWAY_PUBLIC_URL="${GATEWAY_PUBLIC_URL%/}"
ADMIN_USER="$(pick "" KEYCLOAK_ADMIN_USER admin)"
ADMIN_PASSWORD="$(pick "" KEYCLOAK_ADMIN_PASSWORD "")"
[ -n "${ADMIN_PASSWORD}" ] || { echo "KEYCLOAK_ADMIN_PASSWORD is not set (environment or ${ENV_FILE_KC})" >&2; exit 1; }
if [ -z "${SECRETS_FILE}" ] && [ "${NO_SECRETS}" = 0 ] && [ -f .env.agent-gateway ]; then
  SECRETS_FILE=.env.agent-gateway
fi
[ "${NO_SECRETS}" = 0 ] || SECRETS_FILE=""

command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

TMP="$(mktemp -d)"
chmod 700 "${TMP}"
trap 'rm -rf "${TMP}"' EXIT
say() { printf '    %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- The template, with the two placeholders filled -------------------------------------------
jq --arg realm "${REALM}" --arg gw "${GATEWAY_PUBLIC_URL}" '
  walk(if type == "string" then gsub("\\$\\{KEYCLOAK_REALM\\}"; $realm) | gsub("\\$\\{GATEWAY_PUBLIC_URL\\}"; $gw) else . end)
' "${TEMPLATE}" > "${TMP}/tpl.json"
TPL="${TMP}/tpl.json"

# --- Admin API plumbing ------------------------------------------------------------------------
authenticate() {
  # The body (with the password) and the header (with the token) live in 600 files.
  : > "${TMP}/login.body"; chmod 600 "${TMP}/login.body"
  {
    printf 'grant_type=password&client_id=admin-cli&username='
    jq -rjn --arg v "${ADMIN_USER}" '$v|@uri'
    printf '&password='
    jq -rjn --arg v "${ADMIN_PASSWORD}" '$v|@uri'
  } > "${TMP}/login.body"
  local code
  code="$(curl -sS -o "${TMP}/login.out" -w '%{http_code}' --data-binary "@${TMP}/login.body" \
    "${KC_URL}/realms/master/protocol/openid-connect/token")" || die "cannot reach Keycloak at ${KC_URL}"
  [ "${code}" = 200 ] || die "admin sign-in to ${KC_URL} failed (HTTP ${code}); check KEYCLOAK_ADMIN_USER / KEYCLOAK_ADMIN_PASSWORD"
  : > "${TMP}/auth"; chmod 600 "${TMP}/auth"
  printf 'Authorization: Bearer %s\n' "$(jq -r .access_token "${TMP}/login.out")" > "${TMP}/auth"
  rm -f "${TMP}/login.out"
}

CODE=""
kc() {  # kc METHOD PATH [BODYFILE] -- PATH is under /admin/realms; sets CODE, response in $TMP/out
  local method="$1" path="$2" body="${3:-}" attempt
  local args
  for attempt in 1 2; do
    args=(-sS -o "${TMP}/out" -w '%{http_code}' -X "${method}" -H "@${TMP}/auth")
    [ -z "${body}" ] || args+=(-H 'Content-Type: application/json' --data-binary "@${body}")
    CODE="$(curl "${args[@]}" "${KC_URL}/admin/realms${path}")" || die "request to Keycloak failed (${method} ${path})"
    if [ "${CODE}" = 401 ] && [ "${attempt}" = 1 ]; then authenticate; continue; fi
    break
  done
}
ok() {  # ok WHAT -- the last call must have succeeded
  case "${CODE}" in
    2??) ;;
    *) die "$1 failed (HTTP ${CODE}): $(jq -r '.errorMessage // .error // empty' "${TMP}/out" 2>/dev/null || true)" ;;
  esac
}
body() {  # body JSON -- write JSON to a temp file, print its path
  local f; f="$(mktemp "${TMP}/b.XXXXXX")"
  printf '%s' "$1" > "${f}"
  printf '%s' "${f}"
}
json_array() { printf '%s\n' "$@" | jq -R . | jq -sc .; }
enc() { jq -rjn --arg v "$1" '$v|@uri'; }

R="/${REALM}"

# --- 1. The realm ---------------------------------------------------------------------------------
authenticate
kc GET "${R}"
if [ "${CODE}" = 404 ]; then
  kc POST "" "$(body "$(jq -n --arg r "${REALM}" '{realm:$r, enabled:true}')")"
  ok "creating realm ${REALM}"
  say "created realm ${REALM}"
else
  ok "reading realm ${REALM}"
fi
kc PUT "${R}" "$(body "$(jq 'del(.clientScopes,.clients,.groups,.roles,.clientScopeMappings,.serviceAccountRoles)' "${TPL}")")"
ok "updating realm settings"
say "realm settings in place"

# --- 2. Client scopes ----------------------------------------------------------------------------------
kc GET "${R}/client-scopes"; ok "listing client scopes"
cp "${TMP}/out" "${TMP}/scopes.json"
scope_id() { jq -r --arg n "$1" '.[] | select(.name == $n) | .id' "${TMP}/scopes.json"; }

while IFS= read -r scope; do
  name="$(jq -r .name <<<"${scope}")"
  sid="$(scope_id "${name}")"
  if [ -z "${sid}" ]; then
    kc POST "${R}/client-scopes" "$(body "${scope}")"; ok "creating client scope ${name}"
    say "created client scope ${name}"
  else
    kc PUT "${R}/client-scopes/${sid}" "$(body "$(jq 'del(.protocolMappers)' <<<"${scope}")")"; ok "updating client scope ${name}"
    kc GET "${R}/client-scopes/${sid}/protocol-mappers/models"; ok "reading mappers of ${name}"
    cp "${TMP}/out" "${TMP}/mappers.json"
    while IFS= read -r mapper; do
      mname="$(jq -r .name <<<"${mapper}")"
      mid="$(jq -r --arg n "${mname}" '.[] | select(.name == $n) | .id' "${TMP}/mappers.json")"
      if [ -z "${mid}" ]; then
        kc POST "${R}/client-scopes/${sid}/protocol-mappers/models" "$(body "${mapper}")"
      else
        kc PUT "${R}/client-scopes/${sid}/protocol-mappers/models/${mid}" "$(body "$(jq --arg id "${mid}" '. + {id:$id}' <<<"${mapper}")")"
      fi
      ok "applying mapper ${mname} of ${name}"
    done < <(jq -c '.protocolMappers[]' <<<"${scope}")
  fi
done < <(jq -c '.clientScopes[]' "${TPL}")
kc GET "${R}/client-scopes"; ok "listing client scopes"
cp "${TMP}/out" "${TMP}/scopes.json"

# --- 3. Clients --------------------------------------------------------------------------------------------
client_uuid() {  # the uuid of a client by clientId, or empty
  kc GET "${R}/clients?clientId=$(enc "$1")"; ok "looking up client $1"
  jq -r --arg c "$1" '.[] | select(.clientId == $c) | .id' "${TMP}/out"
}

scope_names() {  # scope_names UUID default|optional -- names currently attached
  kc GET "${R}/clients/$1/$2-client-scopes"; ok "listing $2 scopes"
  jq -r '.[].name' "${TMP}/out"
}

reconcile_scopes() {  # reconcile_scopes UUID KIND WANTED_JSON_ARRAY
  local uuid="$1" kind="$2" wanted="$3" other have n sid
  have="$(scope_names "${uuid}" "${kind}")"
  for n in ${have}; do
    jq -e --arg n "${n}" 'index($n)' <<<"${wanted}" >/dev/null && continue
    sid="$(scope_id "${n}")"
    kc DELETE "${R}/clients/${uuid}/${kind}-client-scopes/${sid}"; ok "detaching scope ${n}"
  done
  for n in $(jq -r '.[]' <<<"${wanted}"); do
    sid="$(scope_id "${n}")"
    [ -n "${sid}" ] || die "client scope ${n} does not exist in realm ${REALM}"
    # A scope attached the other way round is moved, not duplicated.
    [ "${kind}" = default ] && other=optional || other=default
    if scope_names "${uuid}" "${other}" | grep -qx "${n}"; then
      kc DELETE "${R}/clients/${uuid}/${other}-client-scopes/${sid}"; ok "moving scope ${n}"
    fi
    kc PUT "${R}/clients/${uuid}/${kind}-client-scopes/${sid}"; ok "attaching scope ${n}"
  done
}

while IFS= read -r client; do
  cid="$(jq -r .clientId <<<"${client}")"
  uuid="$(client_uuid "${cid}")"
  plain="$(jq -c 'del(.defaultClientScopes,.optionalClientScopes)' <<<"${client}")"
  if [ -z "${uuid}" ]; then
    kc POST "${R}/clients" "$(body "${plain}")"; ok "creating client ${cid}"
    uuid="$(client_uuid "${cid}")"
    say "created client ${cid}"
  else
    # Merge over the current representation so what the template does not name (secret, id,
    # an attribute added by hand) is kept.
    kc GET "${R}/clients/${uuid}"; ok "reading client ${cid}"
    merged="$(jq -c --argjson t "${plain}" '. as $c | ($c + $t) | .attributes = (($c.attributes // {}) + ($t.attributes // {}))' "${TMP}/out")"
    kc PUT "${R}/clients/${uuid}" "$(body "${merged}")"; ok "updating client ${cid}"
  fi
  reconcile_scopes "${uuid}" default "$(jq -c '.defaultClientScopes // []' <<<"${client}")"
  reconcile_scopes "${uuid}" optional "$(jq -c '.optionalClientScopes // []' <<<"${client}")"
done < <(jq -c '.clients[]' "${TPL}")
say "clients in place"

# --- 4. Client roles (created when missing, never removed) ---------------------------------------------------------
for cid in $(jq -r '.roles.client | keys[]' "${TPL}"); do
  uuid="$(client_uuid "${cid}")"
  while IFS= read -r role; do
    rname="$(jq -r .name <<<"${role}")"
    kc GET "${R}/clients/${uuid}/roles/$(enc "${rname}")"
    if [ "${CODE}" = 404 ]; then
      kc POST "${R}/clients/${uuid}/roles" "$(body "${role}")"; ok "creating role ${rname}"
      say "created role ${cid}/${rname}"
    else
      ok "reading role ${rname}"
    fi
  done < <(jq -c --arg c "${cid}" '.roles.client[$c][]' "${TPL}")
done

role_reps() {  # role_reps CLIENT_UUID NAME... -- the role representations, as a JSON array
  local uuid="$1"; shift
  kc GET "${R}/clients/${uuid}/roles?max=500"; ok "listing roles"
  jq -c --argjson want "$(json_array "$@")" '[ .[] | select(.name as $n | $want | index($n)) ]' "${TMP}/out"
}

# --- 5. Groups: seeded once, then the operator's ---------------------------------------------------------------------
while IFS= read -r group; do
  gname="$(jq -r .name <<<"${group}")"
  kc GET "${R}/groups?search=$(enc "${gname}")&exact=true"; ok "looking up group ${gname}"
  if [ -n "$(jq -r --arg n "${gname}" '.[] | select(.name == $n) | .id' "${TMP}/out")" ]; then
    continue
  fi
  kc POST "${R}/groups" "$(body "$(jq -c '{name}' <<<"${group}")")"; ok "creating group ${gname}"
  kc GET "${R}/groups?search=$(enc "${gname}")&exact=true"; ok "looking up group ${gname}"
  gid="$(jq -r --arg n "${gname}" '.[] | select(.name == $n) | .id' "${TMP}/out")"
  for cid in $(jq -r '.clientRoles // {} | keys[]' <<<"${group}"); do
    uuid="$(client_uuid "${cid}")"
    mapfile -t names < <(jq -r --arg c "${cid}" '.clientRoles[$c][]' <<<"${group}")
    [ "${#names[@]}" -gt 0 ] || continue
    kc POST "${R}/groups/${gid}/role-mappings/clients/${uuid}" "$(body "$(role_reps "${uuid}" "${names[@]}")")"
    if [ "${CODE:0:1}" != 2 ]; then
      # Do not leave a group without its roles behind: a rerun would take it for the operator's.
      kc DELETE "${R}/groups/${gid}"
      die "giving group ${gname} its roles failed (HTTP ${CODE}); the half-made group was removed"
    fi
  done
  say "created group ${gname}"
done < <(jq -c '.groups[]' "${TPL}")

# --- 6. Client scope mappings: which client roles a client's tokens may carry ------------------------------------------
for owner in $(jq -r '.clientScopeMappings | keys[]' "${TPL}"); do
  owner_uuid="$(client_uuid "${owner}")"
  while IFS= read -r m; do
    target="$(jq -r .client <<<"${m}")"
    mapfile -t names < <(jq -r '.roles[]' <<<"${m}")
    kc POST "${R}/clients/$(client_uuid "${target}")/scope-mappings/clients/${owner_uuid}" "$(body "$(role_reps "${owner_uuid}" "${names[@]}")")"
    ok "scope mapping for ${target}"
  done < <(jq -c --arg o "${owner}" '.clientScopeMappings[$o][]' "${TPL}")
done
say "client scope mappings in place"

# --- 7. Service-account roles: exactly the read-only set ---------------------------------------------------------------
for sa in $(jq -r '.serviceAccountRoles | keys[]' "${TPL}"); do
  sa_uuid="$(client_uuid "${sa}")"
  kc GET "${R}/clients/${sa_uuid}/service-account-user"; ok "reading the service account of ${sa}"
  uid="$(jq -r .id "${TMP}/out")"
  for target in $(jq -r --arg s "${sa}" '.serviceAccountRoles[$s] | keys[]' "${TPL}"); do
    t_uuid="$(client_uuid "${target}")"
    mapfile -t want < <(jq -r --arg s "${sa}" --arg t "${target}" '.serviceAccountRoles[$s][$t][]' "${TPL}")
    kc GET "${R}/users/${uid}/role-mappings/clients/${t_uuid}"; ok "reading roles of ${sa}"
    extra="$(jq -c --argjson want "$(json_array "${want[@]}")" '[ .[] | select(.name as $n | $want | index($n) | not) ]' "${TMP}/out")"
    if [ "${extra}" != "[]" ]; then
      kc DELETE "${R}/users/${uid}/role-mappings/clients/${t_uuid}" "$(body "${extra}")"; ok "removing extra roles of ${sa}"
      say "removed roles from ${sa} that the template does not grant"
    fi
    kc POST "${R}/users/${uid}/role-mappings/clients/${t_uuid}" "$(body "$(role_reps "${t_uuid}" "${want[@]}")")"
    ok "granting roles to ${sa}"
  done
done
say "service accounts in place"

# --- 8. Test people (end-to-end runs only) ------------------------------------------------------------------------------
if [ -n "${TEST_USERS}" ]; then
  touch "${TEST_USERS}"; chmod 600 "${TEST_USERS}"
  for spec in "alice:true:product,leads" "bob:true:engineering" "carol:false:"; do
    uname="${spec%%:*}"; rest="${spec#*:}"; enabled="${rest%%:*}"; groups="${rest#*:}"
    pw="$(env_get "${uname}" "${TEST_USERS}")"
    if [ -z "${pw}" ]; then
      pw="$(openssl rand -hex 16)"
      printf '%s=%s\n' "${uname}" "${pw}" >> "${TEST_USERS}"
    fi
    kc GET "${R}/users?username=$(enc "${uname}")&exact=true"; ok "looking up ${uname}"
    uid="$(jq -r '.[0].id // empty' "${TMP}/out")"
    if [ -z "${uid}" ]; then
      kc POST "${R}/users" "$(body "$(jq -nc --arg u "${uname}" --argjson e "${enabled}" \
        '{username:$u, email:($u+"@example.test"), firstName:$u, lastName:"Test", emailVerified:true, enabled:$e}')")"
      ok "creating ${uname}"
      kc GET "${R}/users?username=$(enc "${uname}")&exact=true"; ok "looking up ${uname}"
      uid="$(jq -r '.[0].id' "${TMP}/out")"
      for g in ${groups//,/ }; do
        kc GET "${R}/groups?search=$(enc "${g}")&exact=true"; ok "looking up group ${g}"
        gid="$(jq -r --arg n "${g}" '.[] | select(.name == $n) | .id' "${TMP}/out")"
        kc PUT "${R}/users/${uid}/groups/${gid}"; ok "adding ${uname} to ${g}"
      done
      say "created test user ${uname}"
    fi
    kc PUT "${R}/users/${uid}/reset-password" "$(body "$(jq -nc --arg p "${pw}" '{type:"password", value:$p, temporary:false}')")"
    ok "setting the password of ${uname}"
  done
fi

# --- 9. The generated client secrets, into the gateway's env file --------------------------------------------------------
set_var() {  # set_var NAME VALUE FILE -- replace the NAME= line, or append one
  local name="$1" value="$2" file="$3" tmp line found=0
  tmp="$(mktemp "${TMP}/e.XXXXXX")"
  while IFS= read -r line || [ -n "${line}" ]; do
    if [ "${found}" = 0 ] && [[ "${line}" =~ ^(#\ ?)?${name}= ]]; then
      printf '%s=%s\n' "${name}" "${value}"; found=1
    else
      printf '%s\n' "${line}"
    fi
  done < "${file}" > "${tmp}"
  [ "${found}" = 1 ] || printf '%s=%s\n' "${name}" "${value}" >> "${tmp}"
  cat "${tmp}" > "${file}"; rm -f "${tmp}"
}

if [ -n "${SECRETS_FILE}" ]; then
  [ -f "${SECRETS_FILE}" ] || { : > "${SECRETS_FILE}"; chmod 600 "${SECRETS_FILE}"; }
  for pair in agent-gateway-directory:DIRECTORY_CLIENT_SECRET agent-gateway-portal:PORTAL_CLIENT_SECRET; do
    cid="${pair%%:*}"; var="${pair#*:}"
    uuid="$(client_uuid "${cid}")"
    kc GET "${R}/clients/${uuid}/client-secret"; ok "reading the secret of ${cid}"
    value="$(jq -r '.value // empty' "${TMP}/out")"
    [ -n "${value}" ] || die "client ${cid} has no secret"
    set_var "${var}" "${value}" "${SECRETS_FILE}"
    say "wrote ${var} to ${SECRETS_FILE}"
  done
else
  say "client secrets not written (no --write-secrets file); read them in the admin console under Clients, Credentials"
fi

say "realm ${REALM} is ready at ${KC_URL}/realms/${REALM}"
