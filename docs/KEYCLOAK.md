# Keycloak

Keycloak does two separate jobs in this stack. They use different clients and can be used
independently:

1. **Identity for the agent gateway, the portal and the Telegram bot** (the `agentflow` realm, section A).
   Keycloak is where people, groups and roles live; the gateway only reads them. This is the identity model
   of [`specs/003-identity-access`](../specs/003-identity-access/spec.md).
2. **Single sign-on for LibreChat** (section B, optional).

This stack does not deploy Keycloak: it assumes one is **already running** somewhere you control (Keycloak
26.x). For development, `make idp-up` starts a throwaway one. The day-to-day how-to is in
[`RUNBOOK_IDENTITY.md`](RUNBOOK_IDENTITY.md).

# A. The `agentflow` realm (agent gateway, portal, Telegram bot)

## The model in one paragraph

A person has **one Keycloak account** (its id, the `sub` claim, is their identity everywhere). They are put
in **groups** (`product`, `engineering`, `leads`, ...), and the groups carry **client roles** of the
`agent-gateway` client (`agent-user`: may use an agent; `agent-writer`: may use write tools). Agents in
`agent-gateway/config/agents/*.yaml` name the groups or roles allowed to use them. Everything about who
exists, which group they are in, and what a group may do is changed **in Keycloak**; the gateway has no
screen or command for it. Access follows team membership: move someone to another group and their access
changes. Disable the account and they are cut off everywhere (Telegram within minutes, keys within one sync
interval).

| Piece | Keycloak object | Used for |
|---|---|---|
| `agent-gateway` | client, no sign-in; owns the roles | the audience of tokens (`aud`) and the home of `agent-user` / `agent-writer` |
| `agent-gateway-portal` | confidential client, standard flow + PKCE | the gateway portal's sign-in |
| `telegram-bot` | public client, device authorization grant | `/login` in the Telegram bot |
| `agent-gateway-directory` | confidential service account, read-only | the gateway's periodic copy of people, groups and roles |
| `agentflow-automation` | disabled template | copy it per automation (service-account token with groups and roles) |

The full contract (mappers, scope mappings, lifetimes) is
[`specs/003-identity-access/contracts/keycloak-realm.md`](../specs/003-identity-access/contracts/keycloak-realm.md);
the template is [`integrations/keycloak/realm/agentflow-realm.json`](../integrations/keycloak/realm/agentflow-realm.json).

## A1. Create the realm

```bash
cp integrations/keycloak/.env.keycloak.example integrations/keycloak/.env.keycloak   # make idp-realm-apply does this
chmod 600 integrations/keycloak/.env.keycloak
# fill in KEYCLOAK_URL, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWORD, GATEWAY_PUBLIC_URL
cp .env.agent-gateway.example .env.agent-gateway   # if you have not yet; the secrets below go into it
make idp-realm-apply
```

No Keycloak yet and just trying it out? `make idp-up` first (a throwaway container on
`127.0.0.1:8480`; `make idp-down` removes it and all its data).

`make idp-realm-apply` creates the realm `agentflow` (only that realm; others are untouched), its clients,
the two custom client scopes, the client roles, example groups and the scope mappings, and writes
`DIRECTORY_CLIENT_SECRET` and `PORTAL_CLIENT_SECRET` into `.env.agent-gateway`. Secrets are never printed.
**It is safe to rerun**: it brings the stack-owned parts (realm settings, scopes and mappers, clients, scope
mappings, the directory account's read-only roles) back to the template, and it leaves alone what you manage:
groups, the roles on a group, users and who is in which group are created once and never changed or removed
by a rerun.

Then set in `.env.agent-gateway`: `OIDC_ISSUER=https://<keycloak-host>/realms/agentflow` (exactly the `iss`
of tokens), `PORTAL_BASE_URL` (the gateway's public address, if you want the portal) and
`PORTAL_SESSION_SECRET`. `KB_TRUST_SECRET` is filled by `make up` (see
[`CONFIGURATION.md`](CONFIGURATION.md)).

## A2. Day to day

All of this is in Keycloak's admin console (or its API), never in the gateway:

1. Create people by hand (**Users, Add user**; set a temporary password). Registration is off.
2. Put them in groups (**Users, Groups tab, Join group**) and give **groups** their roles (**Groups, Role
   mapping, Assign role, filter by clients, `agent-gateway`**). Prefer roles on groups, not on people.
3. Tell them to open the portal and register their model key, and (for Telegram) to send `/login` to the bot.
4. Existing gateway users register again: there is no migration of old keys, stored credentials or threads.
5. To cut someone off: **disable** the account. Their refresh token stops working at once and the gateway
   stops serving them within one sync interval. To end their sessions immediately too: **Users, the user,
   Sessions, Sign out**.

Group and role names: no comma, no leading or trailing space, at most 128 characters; do not nest groups for
access (nesting gives no inheritance here).

Step-by-step with exact clicks and commands: [`RUNBOOK_IDENTITY.md`](RUNBOOK_IDENTITY.md).

## A3. Check it

`scripts/identity-e2e.sh` (`make identity-e2e`) starts a throwaway Keycloak, applies the realm, signs test
people in with the device grant, and checks the token claims, rotation, the read-only directory account and
a disabled person. It does not touch your Keycloak. What it found is in
[`specs/003-identity-access/research.md`](../specs/003-identity-access/research.md), "Verified against
Keycloak 26.8.1".

To look at a token by hand, decode the access token at [jwt.io](https://jwt.io) (use a test token): `aud`
contains `agent-gateway`, `groups` is a list of plain names, and `resource_access.agent-gateway.roles` lists
the roles. A missing role is almost always a role not given to a group the person is in, or a missing client
scope mapping (rerun `make idp-realm-apply`).

# B. Single sign-on for LibreChat (optional)

Optional, pluggable at zero cost: the stack ships on local email/password
auth and switches to Keycloak by uncommenting one `.env` block and
restarting `api` -- no image rebuild, no config-file edit, and it reverts
the same way.

This assumes Keycloak is **already running** somewhere you control; this
stack does not deploy it.

## B1. Create the client

In your Keycloak admin console, in the realm you want LibreChat users to
come from:

1. **Clients -> Create client**
   - Client ID: `librechat`
   - Client authentication: **On** (confidential client)
   - Authentication flow: **Standard flow** only -- leave Direct access
     grants and everything else off
2. **Settings** tab:
   - Valid redirect URIs: `https://<your-chat-host>/oauth/openid/callback`
   - Valid post logout redirect URIs: `https://<your-chat-host>/`
   - Web origins: `https://<your-chat-host>`
3. **Credentials** tab: copy the **Client secret** -- this is `OPENID_CLIENT_SECRET`.

## B2. Create the access-gate role

1. **Realm roles -> Create role**: `librechat-user`
2. Assign it to every user who should be able to log into LibreChat at all.
   No role, no access -- `OPENID_REQUIRED_ROLE` below enforces this.

Two more roles are worth creating now even though nothing in this launch
enforces them yet (per-tool authorization happens in the MCP layer later,
not here): `librechat-product` and `librechat-dev`, for scoping which team's
agents a user can see once that's built.

## B3. Fill in `.env`

Uncomment the whole `OPENID_*` block in `.env`, set `ALLOW_EMAIL_LOGIN=false`,
and fill in:

```
ALLOW_SOCIAL_LOGIN=true
ALLOW_SOCIAL_REGISTRATION=true
OPENID_ISSUER=https://<keycloak-host>/realms/<realm>
OPENID_CLIENT_ID=librechat
OPENID_CLIENT_SECRET=<from step 1>
OPENID_CALLBACK_URL=/oauth/openid/callback
OPENID_SCOPE="openid profile email"
OPENID_SESSION_SECRET=<generate with scripts/generate-secrets.sh>
OPENID_REQUIRED_ROLE=librechat-user
OPENID_REQUIRED_ROLE_PARAMETER_PATH=realm_access.roles
OPENID_REQUIRED_ROLE_TOKEN_KIND=access
OPENID_USE_END_SESSION_ENDPOINT=true
```

`OPENID_REQUIRED_ROLE_PARAMETER_PATH=realm_access.roles` is specifically
where Keycloak puts realm roles in its access token -- this is not a generic
OIDC default, it's Keycloak's own token shape.

## B4. Restart and verify

```bash
docker compose up -d api
```

- Visiting the chat host should redirect to Keycloak's login page instead of
  showing LibreChat's own login form.
- Log in as a user **without** `librechat-user` -- confirm access is refused.
- Log in as a user **with** `librechat-user` -- confirm it succeeds and the
  LibreChat account gets created/linked on first login.

To check the role claim actually lands in the token (useful if step 4 fails
in a confusing way): decode the access token at
[jwt.io](https://jwt.io) after a login attempt and confirm
`realm_access.roles` contains `librechat-user`. If it's missing, the role
wasn't assigned to that user, or a client scope is stripping it.

## Switching back to local auth

Set `ALLOW_EMAIL_LOGIN=true`, comment the `OPENID_*` block back out (or just
leave the values -- `ALLOW_SOCIAL_LOGIN=false` alone is enough to disable
the flow), restart `api`. Existing local accounts are unaffected; accounts
created via Keycloak login remain in Mongo but can't log in again until
Keycloak is re-enabled.
