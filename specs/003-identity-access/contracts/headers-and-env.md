# Contract: headers, errors and environment

## Headers

| Header | Direction | Value | Rule |
|---|---|---|---|
| `Authorization: Bearer <x>` | client → gateway | a Keycloak access token, or a registered model key that exactly one person registered (per `GATEWAY_MODEL_KEY_LOGIN`), or (flag on) an `agk_` key | resolution order in FR-ATH-01, FR-ATH-13, FR-ATH-14. Maximum 8 KiB. |
| `Authorization: Bearer <key>` | gateway → model endpoint | the caller's own model key (no shared fallback) | the caller's own `Authorization` is never forwarded. On the pass-through only `content-type`, `accept`, `user-agent`, `openai-organization`, `openai-project`, `openai-beta`, `x-request-id` and `x-client-request-id` are forwarded (FR-RTE-09). |
| `X-KB-Groups` | gateway → `mcp-kb` | comma-separated group names, no spaces around commas, e.g. `product,engineering` | honoured only with a valid `X-KB-Trust`. Names with commas or control characters are dropped by the sender. At most 64 names, 4 KiB. |
| `X-KB-Trust` | gateway → `mcp-kb` | the value of `KB_TRUST_SECRET` | compared in constant time. Sent only to servers flagged `forward_identity`. Never logged, never in model context. |
| `X-Agent-Skills-Env-<VAR>` | gateway → `agent-skills` | credential value | unchanged by this work. |

Behaviour of `mcp-kb` for the pair:

| `KB_TRUST_SECRET` set in `mcp-kb` | `X-KB-Trust` | `X-KB-Groups` | Caller groups |
|---|---|---|---|
| no | any | any | none (header ignored; one log line at startup says so) |
| yes | missing or different | any | none |
| yes | equal | missing or empty | none |
| yes | equal | list | the list, cleaned |

"None" means unrestricted sources only.

## Error responses

All use OpenAI's error object `{"error": {"message", "type", "param", "code"}}`.

| Situation | Status | `type` | `code` |
|---|---|---|---|
| no bearer, malformed, bad signature, expired, wrong audience/issuer/algorithm, unknown or revoked or expired key, issuing disabled, unknown person, person disabled | 401 | `invalid_request_error` | `invalid_api_key` |
| key caller with a stale directory snapshot | 503 | `server_error` | `identity_unavailable` (header `Retry-After: 30`) |
| no personal model key | 403 | `invalid_request_error` | `upstream_key_required`; message names `PORTAL_BASE_URL/portal` when set |
| provider rejected the person's model key (401/403 from upstream) | 502 | `server_error` | `upstream_key_rejected`; message says the key was refused by the model service and where to replace it |
| agent not permitted | 404 | `invalid_request_error` | `model_not_found` (identical to an unknown model) |
| portal form without valid CSRF or origin | 403 | n/a (HTML) | n/a |

The 401 message never says which check failed (the reason is only in the audit log).

## Environment

### Gateway: `.env.agent-gateway` (root file, unprefixed keys)

| Variable | Default | Secret | Meaning |
|---|---|---|---|
| `GATEWAY_ISSUE_KEYS` | `false` | no | allow gateway-issued `agk_` keys |
| `GATEWAY_MODEL_KEY_LOGIN` | `read-only` | no | `off`, `read-only` or `full`: whether a person's own model key used as the bearer identifies them (only when exactly one person registered it); `read-only` removes write tools and the raw pass-through. Any other value stops startup. `UPSTREAM_API_KEY` no longer exists. |
| `IDENTITY_PROVIDER` | `keycloak` | no | which identity-provider adapter to build; `keycloak` is the only value implemented, any other stops startup and names the allowed values |
| `OIDC_ISSUER` | none (required) | no | issuer URL, e.g. `https://kc.example.org/realms/agentflow` |
| `OIDC_AUDIENCE` | `agent-gateway` | no | required `aud` |
| `OIDC_JWKS_TTL` | `600` | no | seconds the signing keys are cached |
| `DIRECTORY_CLIENT_ID` | `agent-gateway-directory` | no | service account used for the sync |
| `DIRECTORY_CLIENT_SECRET` | none (required) | yes | its secret |
| `DIRECTORY_SYNC_SECONDS` | `300` | no | sync interval |
| `DIRECTORY_MAX_STALE_SECONDS` | `900` | no | key callers are refused beyond this age (must be at least 2 × the interval) |
| `PORTAL_CLIENT_ID` | `agent-gateway-portal` | no | portal's confidential client |
| `PORTAL_CLIENT_SECRET` | empty | yes | its secret |
| `PORTAL_SESSION_SECRET` | empty | yes | signs portal cookies (at least 32 bytes) |
| `PORTAL_BASE_URL` | empty | no | public base URL of the gateway, e.g. `https://gw.example.org`. Empty disables the portal. |
| `KB_TRUST_SECRET` | empty | yes | shared with `mcp-kb`; empty means no identity is forwarded |
| `AUDIT_RETENTION_DAYS` | `365` | no | pruning age |

Startup validation (FR-ATH-12, FR-CFG-04):

- `OIDC_ISSUER`, `OIDC_AUDIENCE` and `DIRECTORY_CLIENT_SECRET` are required: every caller, token or
  key, is resolved to a person through the identity provider (a key caller through the directory
  copy), so a gateway without them could not say who anyone is. Startup fails naming the missing one.
- `PORTAL_BASE_URL` set requires `PORTAL_CLIENT_SECRET` and a `PORTAL_SESSION_SECRET` of at least
  32 bytes, and `OIDC_ISSUER`.
- `DIRECTORY_MAX_STALE_SECONDS` below twice `DIRECTORY_SYNC_SECONDS` fails with a message naming both.
- Every message names the variable. No secret value appears in a message.

The `OIDC_*`, `DIRECTORY_*` and `PORTAL_CLIENT_*` settings are read only by the selected provider's
adapter (`adapters/outbound/identity/keycloak/`) and by the settings validation that asks the adapter
to check them. Nothing in `domain/` or `application/` reads them. A future provider brings its own
setting names and its own validation rules; `IDENTITY_PROVIDER`, `PORTAL_BASE_URL`,
`PORTAL_SESSION_SECRET`, `DIRECTORY_SYNC_SECONDS`, `DIRECTORY_MAX_STALE_SECONDS` and
`GATEWAY_ISSUE_KEYS` stay provider-neutral.

### Knowledge base: `kb/.env` and the `mcp-kb` service

| Variable | Where | Meaning |
|---|---|---|
| `KB_TRUST_SECRET` | `kb/.env.example`; passed to `mcp-kb` in its explicit `environment:` list | the same value as the gateway's `KB_TRUST_SECRET`. Not given to `kb-admin`, `kb-scheduler`. |

`mcp-kb` still receives no env file and no source credential. The root `.env.example` gains none of
these (NFR-CFG-02); the stack tooling (`prepare.sh`) copies the one value into both files when it
generates them.

### Telegram bot: `.env` of the bot (generic names)

See [`bot-requirements.md`](bot-requirements.md).

### Keycloak (stack, unit U9): `integrations/keycloak/.env.keycloak`

| Variable | Meaning |
|---|---|
| `KEYCLOAK_URL` | base URL of the Keycloak server |
| `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD` | bootstrap admin, used by the apply script only |
| `KEYCLOAK_REALM` | realm name (default `agentflow`) |
| `GATEWAY_PUBLIC_URL` | for the portal redirect URI |
| client secrets | generated by the script, written to `.env.agent-gateway` |
