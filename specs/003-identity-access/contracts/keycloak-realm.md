# Contract: the Keycloak realm

What unit U9 provisions with `integrations/keycloak/` (a realm template and an idempotent apply
script). Keycloak 26.x, self-hosted, a **new realm** on the existing server. Nothing in the existing
realms is touched. Values in `${...}` are filled from `integrations/keycloak/.env.keycloak`.

## Realm `agentflow`

| Setting | Value | Why |
|---|---|---|
| `registrationAllowed` | false | accounts are made by hand |
| `loginWithEmailAllowed` | true | |
| `accessTokenLifespan` | 300 | bounds how long a stale token (groups, disabled person) can be used |
| `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` | 1800 / 36000 | defaults, stated so a change is visible |
| `offlineSessionIdleTimeout` | 2592000 (30 days) | how long the bot's refresh token lives without use |
| `revokeRefreshToken` | true, `refreshTokenMaxReuse` 0 | rotation: a stolen, already used refresh token fails and reveals itself |
| `oauth2DeviceCodeLifespan` | 600 | a person has 10 minutes to approve |
| `oauth2DevicePollingInterval` | 5 | |
| `bruteForceProtected` | true | |
| `defaultRoles` | `offline_access`, `uma_authorization` | `offline_access` must be in the default roles or the bot cannot request it |
| `eventsEnabled` | true; `adminEventsEnabled` true | Keycloak's own log of sign-ins |

No users are imported. Groups created (examples, the operator edits them): `product`,
`engineering`, `leads`, `automation`. Client roles created on the `agent-gateway` client (examples):
`agent-user` (may use an agent) and `agent-writer` (may use write tools).

**Who manages what**: groups, roles and their assignment are created and maintained in Keycloak's
admin console (or its API) by an administrator. The gateway only reads them, from tokens and through
the directory copy. The gateway has no screen, command or API that creates a group or role or
assigns one to a person, and its directory service account is read-only.

**Roles follow Keycloak's convention**: realm roles are cross-application and are **not** used for
gateway authorization; the roles the gateway reads are the **client roles of its own client**
(`agent-gateway`), which is where Keycloak expects application-specific roles. Groups are the
organisational unit.

**Recommended administration practice**: assign client roles to **groups**, not to individual
people, and put people in groups. Access then follows team membership: moving someone to another
team changes their access with no per-person role editing, and an audit of "who can write" is a look
at a group. Example:

| Group | Client roles of `agent-gateway` it carries |
|---|---|
| `product` | `agent-user` |
| `engineering` | `agent-user` |
| `leads` | `agent-writer` |

A person in `product` and `leads` therefore has `agent-user` and `agent-writer` (effective roles,
through the groups). Assigning a role directly to a person works too and is read the same way; it is
simply not the recommended practice.

## Client scopes

| Scope | Type | Mapper | Effect |
|---|---|---|---|
| `gateway-audience` | default scope | `oidc-audience-mapper`: `included.client.audience=agent-gateway`, `access.token.claim=true`, `id.token.claim=false` | access tokens carry `aud` containing `agent-gateway`, which `OIDC_AUDIENCE` requires |
| `groups` | default scope | `oidc-group-membership-mapper`: `claim.name=groups`, **`full.path=false`**, `access.token.claim=true`, `id.token.claim=false`, `userinfo.token.claim=false` | `"groups": ["product", "leads"]`: leaf names, the same form the directory sync produces |
| `roles` (built in) | default scope | Keycloak's standard `client roles` mapper, unchanged | the standard claim `resource_access.agent-gateway.roles` (effective client roles, including those inherited through groups and composites). No custom role mapper exists. |
| `profile` (built in) | default scope | built in | `preferred_username` |
| `offline_access` (built in) | optional scope | built in | refresh token that survives the SSO session |

**Where the adapter reads roles**: only `resource_access.<client>.roles` in tokens, where `<client>`
is the client named by `OIDC_AUDIENCE` (`agent-gateway`); and, for the directory copy, the Admin API's
effective client-role mappings of that same client for each user
(`GET /admin/realms/{realm}/users/{id}/role-mappings/clients/{client-uuid}/composite`, with the
client's uuid looked up once by `GET /clients?clientId=agent-gateway`). The effective endpoint
expands composites and roles inherited through groups. Realm roles and other clients' roles are
ignored, so Keycloak's built-in roles (`offline_access`, `uma_authorization`, `default-roles-*`) never
reach the gateway. The port still returns one provider-neutral `roles` tuple; nothing outside the
Keycloak adapter knows where it came from.

**Client scope mappings**: the clients below have `fullScopeAllowed` off, so each client that signs
people in (`telegram-bot`, `agent-gateway-portal`, `agentflow-automation`) gets a scope mapping that
allows the `agent-gateway` client's roles into its tokens. Without it `resource_access` would be
empty.

`full.path=false` is essential: with the default (`true`) a group arrives as `/product` and no group
rule would ever match.

## Clients

| Client id | Type | Flows | Default scopes | Optional scopes | Purpose |
|---|---|---|---|---|---|
| `agent-gateway` | audience only | none (standard, implicit, direct, service accounts all off) | n/a | n/a | exists so tokens can name the gateway as audience, and as the owner of the gateway's client roles (`agent-user`, `agent-writer`); nobody signs in to it |
| `agent-gateway-portal` | confidential | standard flow only; PKCE `S256` required; redirect `${GATEWAY_PUBLIC_URL}/portal/callback`; post-logout redirect `${GATEWAY_PUBLIC_URL}/portal/login` | `openid`, `profile`, `gateway-audience`, `roles` | none | the portal's sign-in |
| `telegram-bot` | public | OAuth 2.0 device authorization grant only (`oauth2.device.authorization.grant.enabled=true`); standard flow, direct access and implicit off | `openid`, `profile`, `gateway-audience`, `groups`, `roles` | `offline_access` | Telegram link; the bot requests `openid offline_access` |
| `agent-gateway-directory` | confidential, service account | client credentials only | none | none | directory sync. Service-account roles from `realm-management`: `view-users`, `query-users`, `query-groups`, `view-clients` (to find the gateway client and read its roles). Nothing else; no `manage-*` role. Because `fullScopeAllowed` is off, these roles must also be scope-mapped to the client, or the token lacks them and every read answers 403. Keycloak's user list leaves service-account users out; the adapter reads them through their clients (`serviceAccountsEnabled`). |
| `agentflow-automation` | confidential, service account, **disabled** in the template | client credentials only | `profile`, `gateway-audience`, `groups`, `roles` | none | template that the operator copies per automation (and for the knowledge-base scheduler if it ever calls the gateway). Its service-account user is placed in groups to give it access. |

Other rules for every client: `fullScopeAllowed` false; wildcard redirect URIs are not used; the
secret of a confidential client is generated by the apply script and written to
`.env.agent-gateway` (never into the template).

## Tokens the gateway expects

An access token from `telegram-bot` or the portal after consent:

```json
{
  "iss": "https://kc.example.org/realms/agentflow",
  "aud": ["agent-gateway", "account"],
  "sub": "5b0d3f2a-...",
  "azp": "telegram-bot",
  "preferred_username": "alice",
  "groups": ["product", "leads"],
  "resource_access": { "agent-gateway": { "roles": ["agent-user", "agent-writer"] } },
  "exp": 1790000000
}
```

A service account's token has `preferred_username` `service-account-<client id>`, its groups and its roles.

## Group and role naming rules (shared with the gateway; groups also with the knowledge base)

No comma, no leading or trailing space, at most 128 characters, no path (do not nest groups for
access purposes, since nesting gives no inheritance here).

## Operator runbook points (documented in `docs/KEYCLOAK.md` by U9)

1. Import the template or run the apply script (creates or updates by name; reruns change nothing).
2. Create people by hand, put them in groups and give them roles (in Keycloak, never in the
   gateway), tell them to open the portal and register their model
   key, and (for Telegram) send `/login` to the bot.
3. Existing gateway users register again (no migration).
4. To cut a person off: disable the account in Keycloak. Their refresh token stops working at once;
   the gateway stops serving them within one sync interval. To also end their sessions immediately,
   use "Sessions, sign out" on the user.

## Verified in the end-to-end run (Keycloak 26.8.1)

Keycloak issues an `offline_access` refresh token (`typ` `Offline`, 30-day idle lifetime) through the
device grant when `offline_access` is requested as an optional scope, so the fallback is not needed. Two
details the table above leaves implicit are part of the template: signing-in clients keep the built-in
`basic` scope (it carries `sub`), and the directory client has a client scope mapping for its
`realm-management` roles (with `fullScopeAllowed` off its token would otherwise not carry them and every
Admin API call would be 403). See [`../research.md`](../research.md), "Verified against Keycloak 26.8.1".
