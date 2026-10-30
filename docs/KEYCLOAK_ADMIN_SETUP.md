# What the Keycloak administrator sets up

agentflow does not create or change anything in Keycloak. Realms, clients, client scopes, roles,
groups, users, group membership and role assignments are all the Keycloak administrator's, done in
Keycloak's admin console. The stack only **reads tokens** (and, optionally, people and groups through a
read-only service account) and enforces access from what they say.

Hand this page to the administrator. When every box below is ticked, the operator fills the values in
section 9 into the stack's env files and starts it. Nothing here is run by agentflow.

The realm name is the administrator's choice. This page writes `<realm>`; the issuer URL the stack is
given is `https://<keycloak-host>/realms/<realm>`. The client ids below are the defaults the env files
use; if the administrator picks others, the operator changes the matching variables (section 9).

The same content as the reference template the stack's own tests use:
[`integrations/keycloak/realm/agentflow-realm.json`](../integrations/keycloak/realm/agentflow-realm.json)
and [`specs/003-identity-access/contracts/keycloak-realm.md`](../specs/003-identity-access/contracts/keycloak-realm.md).
The tooling that applies that template (`make idp-realm-apply`) is for development and the end-to-end
check; it is not how a real realm is made.

---

## 1. Realm settings

| Setting (Realm settings) | Value | Why |
|---|---|---|
| Access token lifespan (Tokens) | 5 minutes (`300`) | A change of groups or roles shows up within this time. |
| SSO session idle / max | 30 minutes / 10 hours | Portal sign-in session. |
| Offline session idle (Tokens) | 30 days | How long a Telegram link survives without use. |
| Revoke refresh token (Tokens) | on, reuse count `0` | A stolen refresh token is single-use. |
| OAuth 2.0 device code lifespan / polling interval | 10 minutes / 5 seconds | The Telegram `/login` flow. |
| Login with email | on | |
| User registration, forgot-password | off | People are created by the administrator, not self-service. |
| Brute-force detection (Security defenses) | on | |
| Events and admin events (Events) | on, keep 90 days | The administrator's own audit trail of sign-ins and changes. |

## 2. Client scopes (Client scopes, Create client scope; protocol OpenID Connect)

Both are **not** shown on the consent screen and **not** included in the token's `scope` claim.

| Scope | Mapper | Settings |
|---|---|---|
| `gateway-audience` | Audience (`oidc-audience-mapper`), name `gateway-audience` | Included client audience: `agent-gateway`. Add to access token **on**, to ID token off, to introspection **on**. |
| `groups` | Group Membership (`oidc-group-membership-mapper`), name `groups` | Token claim name: `groups`. **Full group path off** (names only, no leading `/`). Add to access token **on**, ID token off, userinfo off, introspection **on**. |

Keycloak's built-in scopes `basic`, `profile`, `roles` and `offline_access` must still exist in the realm.
`roles` is what puts `resource_access.<client>.roles` into the access token.

## 3. Client `agent-gateway` (audience and roles only; nobody signs in to it)

Clients, Create client. Client id `agent-gateway`. Client authentication **on**. Turn **off** standard flow,
direct access grants, implicit flow, service accounts. Valid redirect URIs: none. Full scope allowed
(Client scopes, `agent-gateway-dedicated`, Scope): **off**. No default or optional client scopes.

Roles tab, Create role (these are **client roles** of `agent-gateway`, not realm roles):

| Role | Meaning |
|---|---|
| `agent-user` | May use an agent. |
| `agent-writer` | May also use an agent's write tools (Jira/Confluence/GitLab writes). |

Further roles can be added the same way; an agent file refers to them by name (`access.roles: [name]`).

## 4. Client `agent-gateway-portal` (the self-service portal sign-in)

| Field | Value |
|---|---|
| Client id | `agent-gateway-portal` |
| Client authentication | on (confidential) |
| Standard flow | **on** (the only flow); implicit, direct access grants, service accounts **off** |
| PKCE method (Advanced, Advanced settings) | `S256` |
| Valid redirect URIs | `<GATEWAY_PUBLIC_URL>/portal/callback` |
| Valid post-logout redirect URIs | `<GATEWAY_PUBLIC_URL>/portal/login` |
| Web origins | none |
| Full scope allowed | **off** |
| Default client scopes | `basic`, `profile`, `gateway-audience`, `roles` |
| Optional client scopes | none |
| Scope (dedicated scope, Scope tab) | Assign the `agent-gateway` client roles `agent-user` and `agent-writer` |

`<GATEWAY_PUBLIC_URL>` is exactly what browsers use to reach the gateway (e.g. `https://gw.example.org`),
with no trailing slash. **Copy the client secret** (Credentials tab) for section 9.

## 5. Client `telegram-bot` (device login; no secret)

| Field | Value |
|---|---|
| Client id | `telegram-bot` |
| Client authentication | **off** (public client) |
| OAuth 2.0 Device Authorization Grant | **on** (Capability config) |
| Standard flow, implicit, direct access grants, service accounts | **off** |
| Full scope allowed | **off** |
| Default client scopes | `basic`, `profile`, `gateway-audience`, `groups`, `roles` |
| Optional client scopes | `offline_access` |
| Scope (dedicated scope, Scope tab) | Assign the `agent-gateway` client roles `agent-user` and `agent-writer` |

## 6. Client `agent-gateway-directory` (optional: read-only copy of people and groups)

This is the one place the stack calls Keycloak's admin API. It is read-only and **confined to the target
realm**: the client is created in `<realm>`, never in `master`, and its service account holds only the
`<realm>`'s `realm-management` roles listed below. The gateway signs in at
`<issuer>/protocol/openid-connect/token` and reads only `/admin/realms/<realm>/...`; it never uses the
master realm and holds no master-realm credential. The gateway copies people, their groups and their
enabled/disabled state into its own database (`gateway-db`) every sync interval, so it keeps working from
that copy for up to `DIRECTORY_MAX_STALE_SECONDS` (15 minutes) if Keycloak is briefly down. With it, a
disabled person is cut off within one sync interval (5 minutes by default) instead of when their token
expires.

| Field | Value |
|---|---|
| Client id | `agent-gateway-directory` |
| Client authentication | on; **Service accounts roles on**; standard flow, implicit, direct access grants **off** |
| Full scope allowed | **off** |
| Default client scopes | `roles` |
| Service account roles (Service account roles tab, Assign role, filter by clients, `realm-management`) | **Exactly** `view-users`, `query-users`, `query-groups`, `view-clients`. Nothing else. |

**Copy the client secret** for section 9.

## 7. Client `agentflow-automation` (optional template for scripts)

A disabled copy-me template. Create it only if scripts or automation need their own identity.
Client authentication on, service accounts **on**, other flows off, **Enabled off**, full scope **off**,
default scopes `basic`, `profile`, `gateway-audience`, `groups`, `roles`, scope roles `agent-user` and
`agent-writer`. To give one automation an identity: copy it, enable the copy, then put the copy's
service-account user (`service-account-<client id>`) into the groups it needs.

## 8. Groups, roles on groups, people

Everything below is the administrator's ongoing work; the stack never does it.

- **Groups**: a plain name each (no commas, no slashes, no nesting). The groups the stack's example agents
  and knowledge-base sources refer to are `product`, `engineering`, `leads` and `automation`; the names are
  free, they only have to match what agent files (`access.groups`) and knowledge-base sources name.
- **Roles on groups**: Groups, the group, Role mapping, Assign role, filter by clients, `agent-gateway`.
  Give `agent-user` to every group whose people may use agents, `agent-writer` to those who may use write
  tools. Give roles to **groups**, not to individual people.
- **People**: Users, Add user (username, email, first and last name), set a password (temporary on), and Join
  group. Disable a person who leaves (Enabled off, then Sessions, Sign out).
- A person's **account id** (the `sub` claim, Users, the person, Details, ID) is what the gateway files
  their model key, credentials and audit under. Delete-and-recreate loses their gateway data; restore
  Keycloak's database rather than recreating accounts.

## 9. What the operator needs back, and where it goes

| From the administrator | Goes in | Variable |
|---|---|---|
| Issuer: `https://<keycloak-host>/realms/<realm>` (exactly the `iss` in a token) | `.env.agent-gateway` | `OIDC_ISSUER` |
| Same issuer | `integrations/telegram-bot/.env` | `OIDC_ISSUER` |
| `agent-gateway` (the audience) | `.env.agent-gateway` | `OIDC_AUDIENCE` |
| Portal client id and secret | `.env.agent-gateway` | `PORTAL_CLIENT_ID`, `PORTAL_CLIENT_SECRET` |
| Directory client id and secret (if used) | `.env.agent-gateway` | `DIRECTORY_CLIENT_ID`, `DIRECTORY_CLIENT_SECRET` |
| `telegram-bot` (no secret) | `integrations/telegram-bot/.env` | `OIDC_CLIENT_ID` |
| The gateway's public address | `.env.agent-gateway` | `PORTAL_BASE_URL` |

The operator also sets, with no Keycloak involvement: `PORTAL_SESSION_SECRET` (`openssl rand -hex 32`),
`LINK_MODE=device` and `OIDC_SCOPE=openid offline_access` for the bot. The gateway and the bot must be
able to reach the issuer URL **from inside their containers**; `localhost` there is the container.

## 10. How the administrator checks it

Sign in as a test person through the portal or the Telegram bot, then decode the access token
(jwt.io, or `base64 -d` on its middle part) and look for:

- `iss` equal to the issuer in section 9;
- `aud` containing `agent-gateway`;
- `sub` and `preferred_username`;
- `groups`: the person's group names, no `/`;
- `resource_access.agent-gateway.roles`: the roles of their groups (`agent-user`, `agent-writer`).

If `groups` is missing, the client lacks the `groups` scope. If `aud` is missing, it lacks
`gateway-audience`. If `resource_access` is empty, the `roles` scope or the dedicated scope's role mapping is
missing, or the person's groups have no `agent-gateway` roles. A person not in an agent's group gets
"model not found", on purpose.
