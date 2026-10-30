# Identity setup guide: Keycloak to a working login

A step-by-step guide to get people signing in to the agent gateway, its portal and the Telegram bot with
their Keycloak accounts. It assumes nothing: every click is spelled out. Allow about 45 minutes the first
time.

**Who does what.** Everything in Part 1 is done in Keycloak's admin console by the Keycloak
administrator. agentflow never creates or changes anything in Keycloak. Part 2 onward is done on the
machine that runs the stack. If you are both people, do the parts in order.

If you want the same content as a flat checklist for an administrator who does not need the walkthrough,
see [`KEYCLOAK_ADMIN_SETUP.md`](KEYCLOAK_ADMIN_SETUP.md). The operator's day-to-day tasks (add a user, change
a group, rotate a secret) are in [`RUNBOOK_IDENTITY.md`](RUNBOOK_IDENTITY.md).

---

## What you are building

```
person --> Keycloak (signs them in, says who they are, which groups, which roles)
              |
              v  a signed "token"
        agent gateway  (checks the token, lets the person use the agents their groups allow)
```

Keycloak knows **who** people are. The gateway decides **what they may do**, from the groups and roles in
the token. The gateway never stores passwords and never edits Keycloak.

## Your values

Fill this table in once. The rest of the guide refers to the names on the left.

| Name used below | What it is | Example |
|---|---|---|
| `KEYCLOAK_URL` | The address of Keycloak, as you open it in a browser | `http://keycloak-dev:8480` (see Part 2 for why not `localhost`) |
| `REALM` | The exact **realm name** (not its display name) | `agentflow` |
| `GATEWAY_URL` | The address people use for the gateway | `http://localhost:8090` |
| `ISSUER` | `KEYCLOAK_URL/realms/REALM`, written out | `http://keycloak-dev:8480/realms/agentflow` |

> **Find the exact realm name.** In the admin console, open the realm dropdown at the top left, choose your
> realm, and read the address bar: `…/admin/master/console/#/<realm name>/…`. That last part is the realm
> name. It is what goes into `ISSUER`, and it can differ from the title shown in the dropdown.

---

# Part 1. In the Keycloak admin console

Open `KEYCLOAK_URL/admin` and sign in as an administrator. Choose your realm in the top-left dropdown and
**check it says your realm, not `master`, before every step**: it is the most common mistake.

Everything in this part goes into the realm only. Nothing is created in `master`.

## Step 1. Create the `agent-gateway` client (it holds the roles)

Nobody signs in with this client. It exists to name the gateway as the audience of tokens and to own the
two roles.

1. **Clients** → **Create client**.
2. Client type `OpenID Connect`, Client ID `agent-gateway` → **Next**.
3. **Client authentication: On**. Untick **Standard flow** and **Direct access grants** → **Next** → **Save**.
4. Open the **Roles** tab → **Create role** → Role name `agent-user` → **Save**.
   Description (optional): *May use an agent.*
5. Back on **Roles** → **Create role** → `agent-writer` → **Save**. Description: *May use an agent's write tools.*

- [ ] Client `agent-gateway` exists with roles `agent-user` and `agent-writer`.

## Step 2. Create two client scopes

A client scope is a bundle of rules for what a token carries. You need two.

**2a. `gateway-audience`: puts the gateway's name in the token**

1. **Client scopes** → **Create client scope**.
2. Name `gateway-audience`. Type **None**. Protocol `OpenID Connect`. **Display on consent screen: Off**.
   **Include in token scope: Off**. → **Save**.
3. Open the **Mappers** tab → **Configure a new mapper** → **Audience**.
4. Name `gateway-audience`. **Included Client Audience**: `agent-gateway`.
   **Add to ID token: Off**. **Add to access token: On**. **Add to lightweight access token: Off**.
   **Add to token introspection: On**. → **Save**.

**2b. `groups`: puts the person's group names in the token**

1. **Client scopes** → **Create client scope**.
2. Name `groups`. Type **None**. Protocol `OpenID Connect`. Display on consent screen **Off**.
   Include in token scope **Off**. → **Save**.
3. **Mappers** tab → **Configure a new mapper** → **Group Membership**.
4. Name `groups`. **Token Claim Name**: `groups`. **Full group path: Off** (names only, no leading `/`).
   **Add to ID token: Off**. **Add to access token: On**. **Add to userinfo: Off**.
   **Add to token introspection: On**. → **Save**.

- [ ] Client scopes `gateway-audience` and `groups` exist, each with its mapper.

> If your realm has no scope called `roles`, `basic`, `profile` or `offline_access`, create them from
> Keycloak's documentation before going on: the clients below use them. A normal realm has them already.

## Step 3. Create the directory client (the gateway's read-only view of people)

The gateway keeps a copy of your people, groups and who is enabled, and refreshes it every 5 minutes. That
is how a disabled person is cut off quickly. It uses this one account, which can **only read**, and only in
this realm.

1. **Clients** → **Create client**. Client ID `agent-gateway-directory` (use a different name if you like and
   set `DIRECTORY_CLIENT_ID` to match later) → **Next**.
2. **Client authentication: On**. Tick **Service accounts roles**. Untick **Standard flow** and
   **Direct access grants** → **Next** → **Save**.
3. Open the **Service accounts roles** tab → **Assign role**. Change the filter dropdown to
   **Filter by clients**, search `realm-management`, and tick exactly these four:
   - `view-users`
   - `query-users`
   - `query-groups`
   - `view-clients`

   → **Assign**. (`default-roles-<realm>` stays; it is a harmless Keycloak default.)
4. Open the **Credentials** tab and copy the **Client secret**. Keep it for Part 3.

- [ ] Directory client exists with those four roles. Its secret is copied.

## Step 4. Create the portal client (the web page where people store their own keys)

1. **Clients** → **Create client**. Client ID `agent-gateway-portal` → **Next**.
2. **Client authentication: On**. Tick only **Standard flow**. Untick the rest → **Next**.
3. Fill in:
   - **Valid redirect URIs**: `GATEWAY_URL/portal/callback`
   - **Valid post logout redirect URIs**: `GATEWAY_URL/portal/login`
   - Leave **Root URL**, **Home URL** and **Web origins** empty.

   → **Save**.
4. **Advanced** tab → **Advanced settings** → **Proof Key for Code Exchange Code Challenge Method** →
   `S256` → **Save**.
5. **Client scopes** tab → **Add client scope** → tick `gateway-audience` → **Add** → **Default**.
   `basic`, `profile` and `roles` should already be listed as Default.
6. On the same tab open **`agent-gateway-portal-dedicated`** → **Scope** tab:
   - **Full scope allowed: Off**.
   - **Assign role** → filter by clients → search `agent-gateway` → tick `agent-user` and `agent-writer` →
     **Assign**.
7. **Credentials** tab → copy the **Client secret**. Keep it for Part 3.

- [ ] Portal client exists, redirect URIs set, PKCE `S256`, scope done. Its secret is copied.

## Step 5. Create the Telegram client (sign-in without sharing a password with the bot)

The bot shows a short code and an address; the person opens it, signs in to Keycloak, and approves. The bot
never sees the password.

1. **Clients** → **Create client**. Client ID `telegram-bot` → **Next**.
2. **Client authentication: Off** (a public client; it has no secret).
   Tick **OAuth 2.0 Device Authorization Grant**. Untick **Standard flow** and **Direct access grants**
   → **Next** → **Save**.
3. **Client scopes** tab → **Add client scope** → tick `gateway-audience` and `groups` → **Add** →
   **Default**. Check that `offline_access` is listed as **Optional** (add it as Optional if not).
4. Open **`telegram-bot-dedicated`** → **Scope** tab: **Full scope allowed: Off**; **Assign role** →
   `agent-user` and `agent-writer` of `agent-gateway`.

- [ ] Telegram client exists with the device grant. (Nothing to copy: it has no secret.)

## Step 6. Create groups, and yourself as a test person

Roles go to **groups**, never to individual people. A person gets the roles of their groups.

1. **Groups** → **Create group** → name `product` → **Create**. Open it → **Role mapping** → **Assign role**
   → filter by clients → search `agent-gateway` → tick `agent-user` → **Assign**.
2. Optional: create a group `leads` the same way and give it `agent-writer`. People in it may use write
   tools (Jira, Confluence, GitLab writes).
3. **Users** → **Add user**. Fill **Username**, **Email**, **First name**, **Last name** → **Create**.
4. **Credentials** tab → **Set password**. For a test account, switch **Temporary** off. → **Save**.
5. **Groups** tab → **Join group** → tick `product` (and `leads` if you want write tools) → **Join**.

Group names must be plain: no commas, no slashes, no nesting. The names must match what agent files list
under `access.groups` (and what knowledge-base sources list). The stack's examples use `product`,
`engineering`, `leads` and `automation`.

- [ ] A group with `agent-user`, and a user in it with a password.

## Step 7. Optional realm checks

**Realm settings** → **Tokens**: **Access Token Lifespan 5 minutes** (the default). A change of groups or
roles takes up to this long to show. **Realm settings** → **Security defenses** → **Brute force detection**:
on. **Events**: turn on **Save events** if you want Keycloak's own sign-in log.

---

# Part 2. Make the gateway able to reach Keycloak (same-machine setup)

**Skip this part if Keycloak has a normal address that both your browser and the Docker containers can reach**
(e.g. `https://keycloak.example.org`). In that case `KEYCLOAK_URL` is that address.

**Do this part if Keycloak runs in Docker on the same machine as the stack**, as the development Keycloak
(`make idp-up`, container `agentflow-keycloak-dev`) does. The problem: inside a container, `localhost` means
the container itself, and the development Keycloak only listens on the host's `127.0.0.1`. The gateway and
the bot would never find it. They must also find Keycloak under **the same name your browser uses**,
because the token records the address it was issued from, and the gateway compares it character for
character.

The fix is one shared name, `keycloak-dev`, that works for both your browser and the containers.

1. Make your computer resolve the name (needs administrator rights, once; skip it if `grep keycloak-dev /etc/hosts` already prints a line):

   ```bash
   echo "127.0.0.1 keycloak-dev" | sudo tee -a /etc/hosts
   ```

2. Put the Keycloak container on the networks the gateway and the bot use, under that name. The Keycloak
   container name is `agentflow-keycloak-dev` for the development one:

   ```bash
   docker network connect --alias keycloak-dev agentflow_telegram-gateway agentflow-keycloak-dev
   docker network connect --alias keycloak-dev agentflow_gateway-api agentflow-keycloak-dev
   ```

   (The gateway and the bot share `telegram-gateway`; `gateway-api` is a second route for the gateway.
   If one command says the container is already connected, that is fine.)
3. Use `http://keycloak-dev:8480` as `KEYCLOAK_URL` from now on, **including in the browser**. Open
   `http://keycloak-dev:8480/admin`, not `localhost`. The dev Keycloak listens on port 8480 inside the
   container too, so the port is the same everywhere.

   If you already created the realm while browsing at `localhost:8480`, nothing is lost: it is the same
   Keycloak. Only the address you type changes.

The connection in step 2 lasts until the Keycloak container is removed (`make idp-down` or `docker rm`).
Repeat the two `docker network connect` commands after recreating it.

For the Telegram sign-in, the address the bot shows (`http://keycloak-dev:8480/realms/.../device`) opens on this
machine only. For people on other devices the real Keycloak needs a public address.

---

# Part 3. Fill in the stack's settings

Use any editor. `nano` is fine; **do not use `$EDITOR`**, it may be empty and zsh will try to run the file.

## `.env.agent-gateway` (in the repository root)

```bash
nano .env.agent-gateway
```

Add these lines (replace the angle-bracket parts; nothing else here is secret except the three secrets):

```
OIDC_ISSUER=<ISSUER>
OIDC_AUDIENCE=agent-gateway
DIRECTORY_CLIENT_ID=agent-gateway-directory
DIRECTORY_CLIENT_SECRET=<secret from Step 3>
PORTAL_BASE_URL=<GATEWAY_URL>
PORTAL_CLIENT_ID=agent-gateway-portal
PORTAL_CLIENT_SECRET=<secret from Step 4>
PORTAL_SESSION_SECRET=<a random 64-character string>
```

| Setting | What it is |
|---|---|
| `OIDC_ISSUER` | Where tokens come from. Must equal the `iss` inside a token exactly, e.g. `http://keycloak-dev:8480/realms/agentflow`. No trailing slash. |
| `OIDC_AUDIENCE` | The name tokens must be addressed to: the `agent-gateway` client of Step 1. |
| `DIRECTORY_CLIENT_ID` / `_SECRET` | The read-only account of Step 3. Use the client id you chose there. |
| `PORTAL_BASE_URL` | The gateway's address as browsers see it, `GATEWAY_URL`. Turns the portal on. |
| `PORTAL_CLIENT_ID` / `_SECRET` | The portal client of Step 4. |
| `PORTAL_SESSION_SECRET` | Signs the portal's cookies. Not from Keycloak. Make one with `openssl rand -hex 32`. |

Make the random string:

```bash
openssl rand -hex 32
```

Also needed once: `KB_TRUST_SECRET` is generated for you by `make up`, which also copies it into this file. If
you have not run `make up` since this branch was checked out, run it once before starting the gateway.

Keep the file private:

```bash
chmod 600 .env.agent-gateway
```

## `integrations/telegram-bot/.env`

```bash
nano integrations/telegram-bot/.env
```

Keep your existing `BOT_TOKEN` and `DEFAULT_MODEL`. Set or add:

```
LINK_MODE=device
OIDC_ISSUER=<ISSUER>
OIDC_CLIENT_ID=telegram-bot
OIDC_SCOPE=openid offline_access
```

`<ISSUER>` is the same value as above. It must be reachable from the bot's container (Part 2).

---

# Part 4. Start and check

```bash
make gateway-up
```

```bash
make telegram-up
```

```bash
make ps
```

`agent-gateway` and `telegram-bot-gateway` should both say healthy. If one is not, read its log:

```bash
make logs SERVICE=agent-gateway
```

## Check the web portal

1. Open `GATEWAY_URL/portal` in your browser.
2. It sends you to Keycloak. Sign in as your test person.
3. You land back on the portal. Open **Model key**, paste your model key and save. (Afterwards the page only
   says that a key is set.)

## Check Telegram

1. In a private chat with the bot, send `/login`.
2. Open the address it shows, sign in, and click **Yes** on the consent page.
3. The bot says it is linked. Send a message; you should get a reply.

## Check what a token says (optional)

If something is refused, the quickest diagnosis is to look at the token. Paste an access token into a token
decoder you trust (never one with a real production token) and look for:

| Claim | Should be |
|---|---|
| `iss` | exactly your `ISSUER` |
| `aud` | contains `agent-gateway` |
| `groups` | your group names, without a leading `/` |
| `resource_access` → `agent-gateway` → `roles` | `agent-user` (and `agent-writer`) |

---

# When something goes wrong

| What you see | Most likely cause | Fix |
|---|---|---|
| Keycloak page: *Realm does not exist* | Wrong realm name in the address, or the realm was lost when a throwaway Keycloak was recreated | Check the exact name (box above). A development Keycloak loses everything when its container is removed. |
| `make gateway-up` exits, the log says `missing environment` | A variable from Part 3 is empty or missing | Add it to `.env.agent-gateway`. |
| Gateway log: cannot fetch signing keys / directory sync failing, `connection refused` | The container cannot reach Keycloak | Part 2, and use the shared name in `OIDC_ISSUER`. |
| Sign-in works but chats say *model not found* | The person is in no group with `agent-user`, or the agent's `access.groups` names another group | Step 6; check the agent file. A change takes up to 5 minutes (or `docker compose exec agent-gateway python -m agent_gateway directory sync`). |
| Chats refused, audit says `bad_issuer` | `OIDC_ISSUER` does not match the token's `iss` character for character | Open the portal and Keycloak at the same address you put in `OIDC_ISSUER`. |
| Chats refused, audit says bad audience | Client lacks the `gateway-audience` scope | Step 2a, then add it to the client (Steps 4 and 5). |
| Token has no `groups` | Client lacks the `groups` scope | Step 5 (Telegram) scope list; for the portal the groups come from the directory copy. |
| Token has no roles | Client's dedicated **Scope** has no roles, or **Full scope allowed** is on with no mapping, or the person's groups have no `agent-gateway` roles | Steps 4/5 (dedicated scope), Step 6 (group role mapping). |
| Portal: *invalid redirect uri* | Redirect URI in Keycloak differs from `PORTAL_BASE_URL` + `/portal/callback` | Step 4, point 3, character for character. |
| Telegram: *sign-in service unavailable* | Bot's container cannot reach `OIDC_ISSUER` | Part 2. |
| Someone left and still works | Directory sync has not run yet, or their token has not expired | They are cut off within 5 minutes. Force it: `docker compose exec agent-gateway python -m agent_gateway directory sync`. |

To see why a particular person was refused:

```bash
make gateway-audit SINCE=1h
```

It names the reason (`person_disabled`, `directory_stale`, …) and never contains a secret.

## Things to know

- **Back up Keycloak yourself.** `make backup` saves the gateway's data and env files, not Keycloak's
  database. Take a realm export (Realm settings → Action → Partial export) after big changes.
- **A person's account id is their identity** in the gateway. If an account is deleted and recreated, the
  gateway data filed under the old id (model key, credentials) is lost. Restore Keycloak's database instead
  of recreating accounts.
- **A development Keycloak is temporary.** `make idp-down` removes it and everything in it. For anything
  you want to keep, use a Keycloak with its own database and backups.
