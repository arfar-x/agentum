# Contract: the portal (HTTP)

A small server-rendered web page set on the gateway's own application, under `/portal`. Built by
unit U4 in `adapters/inbound/portal/` (Jinja2 templates, autoescaped). Present only when
`PORTAL_BASE_URL` is set; otherwise every `/portal/*` path is 404.

Authentication of the portal is its own (a signed session cookie), never the API bearer. No portal
route accepts `Authorization`.

## Common rules

- Every response: `Cache-Control: no-store`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'`. No inline script.
- Every state-changing request is a `POST` form with the field `csrf` equal to the session's `csrf`
  (compared in constant time) **and** an `Origin` (or, absent that, `Referer`) whose origin equals the
  origin of `PORTAL_BASE_URL`. Failing either: 403 and nothing happens.
- A signed-out request to any route except `/portal/login`, `/portal/callback` and
  `/portal/static/*` gets `303` to `/portal/login?next=<path>`; `next` must be a path under `/portal`.
- Secret inputs are `type=password`, `autocomplete=off`, never prefilled. No response contains a
  stored secret, ever. Tests assert this with a sentinel.
- Every successful change writes an audit event in the same transaction (FR-PRT-12).
- Errors are shown as plain messages on the same page with status 400, 403 or 409; never a stack trace.

## Routes

| Method, path | Purpose | Notes |
|---|---|---|
| `GET /portal` | redirect to `/portal/key` | |
| `GET /portal/login` | start sign-in | creates `state`, `nonce`, PKCE `code_verifier`; sets `portal_login` cookie; 302 to the issuer's authorisation endpoint with `response_type=code`, `scope=openid profile`, `code_challenge_method=S256` |
| `GET /portal/callback` | finish sign-in | checks `state`, exchanges the code (client secret + verifier), verifies the ID token (signature, `iss`, `aud`=`PORTAL_CLIENT_ID`, `nonce`, `exp`), requires an enabled directory row (refreshing once if the person is unknown), sets `portal_session`, clears `portal_login`, 303 to `next` or `/portal` |
| `POST /portal/logout` | sign out | clears the cookie; redirects to the issuer's end-session endpoint with `post_logout_redirect_uri=PORTAL_BASE_URL/portal/login` |
| `GET /portal/key` | my model key | shows the active key's `label`, `prefix` (as "ends in …abcd"), `created_at`, `last_used_at`, or "none"; forms below |
| `POST /portal/key` | register or replace | fields `label` (optional), `secret` (required, 8 to 512 chars, no whitespace); 303 to `/portal/key` with a flash message; `409` if the key is registered already (message does not say by whom) |
| `POST /portal/key/revoke` | revoke the active key | |
| `GET /portal/credentials` | my tool credentials | one card per server in `config/mcp-servers.yaml` that declares credentials; each declared variable shows `label`, "set" or "not set", `updated_at`; never a value |
| `POST /portal/credentials/{server}/{var}` | set or replace | field `value`; the pair must be declared, else 404 |
| `POST /portal/credentials/{server}/{var}/delete` | delete one variable | |
| `POST /portal/credentials/{server}/test` | test call | only for a server that declares `test`; runs the declared read-only tool with the person's own credentials; shows "works" or the short error text with credentials masked; audit `cred.test` |
| `GET /portal/issued` | gateway-issued keys | 404 unless `GATEWAY_ISSUE_KEYS` is true; lists the person's issued keys |
| `POST /portal/issued` | create | fields `label`, `expires_in_days` (1 to 365, default 90), `agents` (multi-select of agents the person may use, empty = all), `write` (checkbox); shows the secret **once** on the response page, with the page marked `no-store` |
| `POST /portal/issued/{id}/revoke` | revoke | only the person's own keys |
| `GET /portal/static/*` | stylesheet | fixed files only |
| `GET /portal/healthz` | liveness of the portal | unauthenticated, no data |

The model key is also visible on the credentials page as the read-only line "Model key: set / not
set" with a link to `/portal/key`; it is not editable there.

## Session and sign-in details

- Cookie formats and lifetimes: data model, section 8.
- State, nonce and verifier are single-use. A callback with an unknown or expired `portal_login` is
  400 with a "start again" link.
- The ID token is verified with the same JWKS cache as the API (one verifier, two audiences).
- Only the `sub` and `username` from the ID token are kept. Groups are read from the directory copy
  when a page needs them (the issued-keys form).
- The portal never calls the identity provider's admin API.

## Credential declarations used by the page

In `config/mcp-servers.yaml` (full schema in [`yaml-schemas.md`](yaml-schemas.md)):

```yaml
credentials:
  - name: JIRA_PASSWORD
    label: Jira password or personal access token
    secret: true
    required: true
test:
  tool: jira_my_work          # read-only tool; called with the person's credentials
  args: {}
```

A plain string entry (the form used today) remains valid and means `{name: <string>, label: <string>,
secret: true, required: false}`.
