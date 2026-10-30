# Quickstart: the end-to-end recipe

Run by the coordinator after the units are merged (and in part by each unit for its own module).
`scripts/identity-e2e.sh` (unit U9) automates steps 2 to 9. Everything runs on one machine with
throwaway containers; nothing here touches a real Keycloak or model.

## 0. Module suites (no services)

```bash
cd agent-gateway && python -m pytest -q          # includes spec-coverage and boundary gates
cd kb && python -m pytest -q
cd telegram-bot-gateway && python -m pytest -q
```

The provider contract suite (`tests/providers/`) runs as part of the gateway suite and needs no
service.

Then the service-backed suites: `agent-gateway/scripts/test-services.sh`, `make kb-test`, and the
bot's `scripts/test-services.sh`.

## 1. A throwaway Keycloak with the realm

```bash
docker run -d --name kc-e2e -p 8480:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.8.1 start-dev
```

Apply the realm template and create the test people and client secrets (the template is applied through
the admin API, not `--import-realm`, which would drop Keycloak's built-in client scopes):

```bash
KEYCLOAK_ADMIN_PASSWORD=admin integrations/keycloak/apply.sh --url http://localhost:8480 --realm agentflow \
  --test-users /tmp/e2e-users
```

`scripts/identity-e2e.sh` does all of this (and steps 3, 4 and the Keycloak half of 7) in one go.

Roles and groups are created and assigned in Keycloak, not in the gateway. The script does what an
administrator would do in the admin console: it creates the client roles `agent-user` and
`agent-writer` on the `agent-gateway` client, the groups, the client-role scope mappings for the
signing-in clients, and the assignments below. Following the recommended practice, roles are
assigned to **groups** (`product` and `engineering` carry `agent-user`, `leads` carries
`agent-writer`) and people are put in groups; nobody gets a role directly.

The script creates two test people, `alice` (groups `product`, `leads`, so effective roles `agent-user` and `agent-writer`) and `bob`
(group `engineering`, so `agent-user`), and one disabled person, `carol`. It writes the generated client secrets to
`.env.agent-gateway` for the run (or to `--write-secrets FILE`).

## 2. Gateway, stub model, catalog

Start a stub OpenAI-compatible model that records the `Authorization` header of each request
(`scripts/stub-model.py`, part of U9) on port 9100, then the stack pieces:

```bash
GATEWAY_ISSUE_KEYS=false UPSTREAM_BASE_URL=http://host.docker.internal:9100/v1 make gateway-up
make up            # kb and the rest of the stack
```

Three test agents are used: `open-agent` (no `access`), `product-agent`
(`access.groups: [product]`, `write_groups: [leads]`) and `role-agent`
(`access.roles: [agent-user]`, `write_roles: [agent-writer]`).

## 3. Device sign-in, as the Telegram bot does it

```bash
CID=telegram-bot; R=http://localhost:8480/realms/agentflow/protocol/openid-connect
curl -s -d "client_id=$CID" -d "scope=openid offline_access" $R/auth/device      # note device_code, user_code
```

Approve for `alice` (browser pane at `verification_uri_complete`, or through the admin API in the
script). Then poll:

```bash
curl -s -d "client_id=$CID" -d grant_type=urn:ietf:params:oauth:grant-type:device_code \
     -d device_code=$DEVICE_CODE $R/token
```

Expect `authorization_pending` until approval, then `access_token` and `refresh_token`. Decode the
access token: `aud` contains `agent-gateway`, `groups` is `["product","leads"]`, no slashes, and
`resource_access["agent-gateway"].roles` is `["agent-user","agent-writer"]`.

**Open risk R1**: note `refresh_expires_in` and the refresh token's `typ` claim. `Offline` with a
30-day idle value means `offline_access` was honoured through the device grant. If it is an
ordinary `Refresh` with the SSO session lifespan, apply the fallback in
[`research.md`](research.md) and record which one holds.

## 4. Call the gateway with the token

```bash
curl -s -H "Authorization: Bearer $ALICE_TOKEN" http://localhost:8080/v1/models | jq '.data[].id'
```

Expect all three agents for Alice. With Bob's token expect `open-agent` and `role-agent` (by role), not `product-agent`. Request `product-agent` as
Bob: `404 model_not_found`. Move Bob out of `engineering` in Keycloak (his role came from the group): after a new token (or the
next sync, for his model key) `role-agent` disappears. Nothing in the gateway can grant or remove it.

## 5. Register the model key and run a turn

```bash
python -m agent_gateway key register-llm --owner "$ALICE_SUB" --label e2e   # reads the key from stdin
```

(or through `/portal/key`, signed in as Alice.) Then a chat as Alice with `model=product-agent`.
The stub must log `Authorization: Bearer <Alice's key>` for the agent turn. Repeat with a
pass-through model name and with `GET /v1/models` (SC-006). Without a registered key and with no
`UPSTREAM_API_KEY`, expect `403 upstream_key_required` naming the portal address.

## 6. The model key as a bearer

```bash
curl -s -H "Authorization: Bearer $ALICE_LLM_KEY" http://localhost:8080/v1/models
```

Same listing as step 4 (groups come from the directory copy). Check `audit list` shows
`key.register`.

## 7. Disabled person, stale directory, outage

1. Disable Alice in Keycloak. Wait for a sync (or `python -m agent_gateway directory sync`).
   Both bearers now get 401; a refresh at the token endpoint returns `invalid_grant`.
2. Re-enable. Stop Keycloak (`docker stop kc-e2e`). With a token still unexpired and with the model
   key: both keep working. After `DIRECTORY_MAX_STALE_SECONDS` (set to 120 for the run, with
   `DIRECTORY_SYNC_SECONDS=30`) the key bearer gets `503 identity_unavailable`; an expired token
   gets 401. Start Keycloak: the key bearer recovers after the next sync.
3. `audit list` shows `auth.denied` with reasons `person_disabled` and `directory_stale`, and
   `directory.sync_failed` then `directory.sync_recovered`.

## 8. Knowledge-base groups

Configure two sources, `public` (no groups) and `eng` (`groups: [engineering]`), run
`make kb-sync` for both, then:

- Search through the gateway as Alice: only `public` entries. As Bob: both.
- Call `mcp-kb` directly (from a container on `backend`) with `X-KB-Groups: engineering` and no
  `X-KB-Trust`, and with a wrong one: only `public`. With the right one: both.
- `kb_get` an `eng` entry as Alice: identical to a missing id.

## 9. Portal

With the browser pane: open `/portal`, sign in as Alice, set a Jira credential with a sentinel value,
reload and confirm only "set" is shown; view source and confirm the sentinel is absent. Post the
form with the CSRF field removed: 403. Turn `GATEWAY_ISSUE_KEYS` on and confirm the issued-keys
card appears; off, it is gone and a hand-made POST is refused (SC-007).

## 10. Telegram

With the bot in `LINK_MODE=device` and a stub Telegram API (the bot's own test harness) or a real
bot token on a test account: `/login`, approve, send a message, receive the agent's answer; `/logout`
and confirm the next message asks to link and that the refresh token is revoked at Keycloak.

## 11. Clean up

```bash
docker rm -f kc-e2e; make gateway-down
```

## What a pass looks like

SC-001 to SC-009 in [`spec.md`](spec.md) each map to a step: SC-001 step 3 to 5, SC-002 step 7,
SC-003 step 7, SC-004 steps 5 and 9 (sentinel searches over logs and audit), SC-005 step 8, SC-006
step 5, SC-007 step 9, SC-008 run the existing LibreChat smoke test (`make test` plus a search from
LibreChat), SC-009 step 0.
