# Runbook: people, access and keys

One page for the operator. Find "I want to..." and do exactly what it says. You do not need to know
Keycloak: the glossary at the end explains the five words you will meet. Background and the why:
[`KEYCLOAK.md`](KEYCLOAK.md); the design: [`specs/003-identity-access`](../specs/003-identity-access/spec.md).

**The one rule**: *who exists, which group they are in and what a group may do* is changed in **Keycloak's
admin console** (`https://<keycloak-host>/admin`, realm **agentflow**, chosen in the top-left dropdown). The
gateway never creates people, groups or roles. *A person's own model key and tool credentials* are set in the
gateway's **portal** (`<gateway address>/portal`) by the person, or with `make` by you.

Commands marked **(after the gateway units merge)** exist once the gateway's identity changes are in your
checkout; the clicks in Keycloak work today.

---

## Set up (once)

| I want to | Do this |
|---|---|
| Try it on my machine (development only) | `make idp-up`, then `make idp-realm-apply`. The admin console is `http://localhost:8480/admin`, user `admin`, password in `integrations/keycloak/.env.keycloak` (`KEYCLOAK_ADMIN_PASSWORD`). `make idp-down` throws it all away. `idp-realm-apply` refuses any address but `localhost` / `127.0.0.1`. |
| Get the realm on my real Keycloak | Ask the Keycloak administrator to follow [`KEYCLOAK_ADMIN_SETUP.md`](KEYCLOAK_ADMIN_SETUP.md); this stack creates nothing there. They hand back the issuer URL, the client ids and the two client secrets. |
| Point the gateway at it | In `.env.agent-gateway` set `OIDC_ISSUER=https://<keycloak-host>/realms/<realm>`, `DIRECTORY_CLIENT_ID` / `DIRECTORY_CLIENT_SECRET` and `PORTAL_CLIENT_ID` / `PORTAL_CLIENT_SECRET` from the administrator (section 9 of the setup page). Then `make gateway-up`. |

## People and access

| I want to | Do this |
|---|---|
| **Create a user** | Admin console, **Users, Add user**. Fill username, email, first and last name, **Create**. Tab **Credentials, Set password**, switch **Temporary** on, **Save**. Send them the address and the password. |
| **Add a user to a group** | **Users**, click the person, tab **Groups, Join group**, tick the group, **Join**. They get the group's roles automatically. Their next sign-in (or within 5 minutes) shows it. |
| **Remove a user from a group** | Same tab, tick the group, **Leave**. |
| **Give a group a role** | **Groups**, click the group, tab **Role mapping, Assign role**, change the filter to **Filter by clients**, search `agent-gateway`, tick `agent-user` (may use an agent) or `agent-writer` (may use write tools), **Assign**. Give roles to groups, not to people. |
| **Take a role away from a group** | Same tab, tick the role, **Unassign**. |
| **Create a group** | **Groups, Create group**, a plain name (no commas, no slashes, no nesting), **Create**. Then give it a role as above, and refer to it in an agent file (`access.groups: [name]`). |
| **Create a new role** | **Clients, `agent-gateway`, Roles, Create role**. Refer to it in an agent file (`access.roles: [name]`). |
| **Disable a user** (someone left) | **Users**, the person, switch **Enabled** off, **Save**. Then **Sessions** tab, **Sign out** to end what is open. Their Telegram link and refresh tokens stop working at once; the gateway stops serving them within one sync interval (default 5 minutes). Their stored data stays until you purge it (below). |
| **Re-enable a user** | Same switch on. They may need to `/login` in Telegram again. |
| **Find a person's account id** (the `OWNER` below) | **Users**, the person, tab **Details**, the **ID** field (looks like `5b0d3f2a-...`). |
| **Delete everything the gateway holds for a person** | `make gateway-owner-purge OWNER=<id>` (after the gateway units merge). Irreversible: their model key, tool credentials, keys, threads. Do it after disabling them, if the data should go. |

## Keys and credentials

| I want to | Do this |
|---|---|
| **Register a person's LLM key** | The person: open `<gateway address>/portal`, sign in, **Model key**, paste, save. Or you: `make gateway-key-register OWNER=<id> LABEL=work` and type the key (after the gateway units merge). One active key per person; registering again replaces it. |
| **Add a Jira (or other tool) credential** | The person: the portal, **Tool credentials**, fill the form, save (afterwards it only says "set"). Or you: `make gateway-creds-set OWNER=<id> SERVER=agent-skills VAR=JIRA_PASSWORD` and type the value (after the gateway units merge). Names only, never values: `make gateway-creds-list OWNER=<id>`. |
| **Revoke a key** | `make gateway-key-list OWNER=<id>` to see the ids, then `make gateway-key-revoke KEY_ID=<key id>` (after the gateway units merge). |
| **Issue a gateway key** (for a script that cannot sign in) | Only if you set `GATEWAY_ISSUE_KEYS=true` in `.env.agent-gateway` and restarted the gateway: `make gateway-key-create OWNER=<id> LABEL=ci`. It prints the key once. Prefer a service account (below). |
| **Give a script or automation its own identity** | Admin console, **Clients**, copy `agentflow-automation` (or create a client with the same settings), turn it **Enabled** on, copy the secret from **Credentials**, then **Service account roles**: nothing to add. Under **Users**, find `service-account-<client id>` and **Join group** the groups it needs. The script asks for a token with the client-credentials grant and sends it as the bearer. |
| **Rotate a client secret** | Admin console, **Clients**, the client (`agent-gateway-portal` or `agent-gateway-directory`), tab **Credentials, Regenerate**. Paste the new value into `.env.agent-gateway` and run `make gateway-up` (restarts the gateway with it). The portal and directory sync are down between the regenerate and the restart, so do both at once. The Telegram client is public and has no secret. |

## Telegram

| I want to | Do this |
|---|---|
| **Link a person's Telegram** | In `integrations/telegram-bot/.env` set `LINK_MODE=device` and `OIDC_ISSUER=https://<keycloak-host>/realms/agentflow`, then `make telegram-up`. The person sends `/login` to the bot in a private chat, opens the address it shows, signs in with their Keycloak account, clicks **Yes** on the consent page, and the bot says it is linked. Nothing for you to approve. |
| **Unlink** | The person sends `/logout`. Or you: `make telegram-links`, then `make telegram-unlink TG_ID=<id>`. Disabling the account in Keycloak also stops it. |

## Looking and checking

| I want to | Do this |
|---|---|
| **Check the audit log** | `make gateway-audit` (last 50), `make gateway-audit SINCE=1d ACTION=auth.denied`, `make gateway-audit ACTOR=<id>` (after the gateway units merge). It records who registered a key, stored a credential, was refused and why. It never contains a secret. Keycloak has its own log of sign-ins: **Events** in the admin console. |
| **See why someone cannot get in** | `make gateway-audit SINCE=1h ACTOR=<id>` for the reason (`person_disabled`, `directory_stale`, ...). A person who is not in the agent's group sees "model not found", on purpose. |
| **Make a change show up now** | Tokens refresh every 5 minutes on their own. For the gateway's copy of people and groups: `docker compose exec agent-gateway python -m agent_gateway directory sync` (after the gateway units merge). |
| **Test the whole thing** | `make identity-e2e` starts a throwaway Keycloak and checks sign-in, groups, roles and disabling. It never touches your real one. |

## Backup and restore

| I want to | Do this |
|---|---|
| **Back up** | `make backup` saves the gateway's database, `.env.agent-gateway` (with `CREDS_KEY`), `kb/.env` and `integrations/keycloak/.env.keycloak` together. **Keycloak's own database** (its people, groups, secrets) is not part of this stack: back it up with whatever backs up that server, and take a realm export (**Realm settings, Action, Partial export**) after big changes. |
| **Restore** | `make restore DIR=backups/<timestamp>` restores the gateway's data with the matching env files. Restore them **together**: with a different `CREDS_KEY` the stored model keys and credentials are unreadable and people register again. After restoring Keycloak from its own backup, `make idp-realm-apply` re-creates anything stack-owned that is missing. |
| **Rebuild Keycloak from nothing** | The Keycloak administrator redoes [`KEYCLOAK_ADMIN_SETUP.md`](KEYCLOAK_ADMIN_SETUP.md) and recreates people and group membership. People keep their gateway data only if their account **ID** is the same, so restore Keycloak's database rather than recreating accounts by hand. |

## When Keycloak is down

What keeps working, and for how long (defaults):

- **Chats with a token already issued**: until it expires, at most 5 minutes. The provider's signing keys are
  cached, so a short outage is mostly invisible.
- **Chats with a registered model key**: while the gateway's copy of people is at most 15 minutes old
  (`DIRECTORY_MAX_STALE_SECONDS`); after that they get `503 identity_unavailable` ("try again"), not a
  "wrong key" error.
- **Not working**: new Telegram `/login`, portal sign-in, token refresh. People see "sign-in service
  unavailable" or are asked to try again; nothing is lost. The gateway catches up on its own at the next sync
  after Keycloak is back (`make gateway-audit ACTION=directory.sync_recovered`).

What to do: check it is running (`curl -s -o /dev/null -w '%{http_code}' https://<keycloak-host>/realms/agentflow`
should print 200), restart it, wait. Nothing needs changing in this stack. If you were mid-way disabling
someone, the account is disabled in Keycloak already and is cut off as soon as sync can run.

---

## Concepts to learn (five minutes)

- **Realm**: one Keycloak "tenant": its own people, groups, roles and clients. This stack uses a realm called
  `agentflow` and does not touch the others on the same server.
- **Group**: a team (`product`, `engineering`). People belong to groups. Agents can be restricted to groups,
  and so can sources in the knowledge base. Group names are plain: no commas, no nesting.
- **Client role**: a permission that belongs to one application ("client"). Here the application is
  `agent-gateway` and the roles are `agent-user` and `agent-writer`. You give the roles to **groups**; a person
  has the roles of all their groups. (There are also *realm roles*; this stack ignores them.)
- **Client**: an application that signs people in or asks for tokens: the portal, the Telegram bot, the
  directory sync, an automation. Each has its own settings and, when it can keep one, a secret.
- **JWT (token)**: the signed note Keycloak hands an application after sign-in, valid for 5 minutes. It says
  who the person is (`sub`, `preferred_username`), their `groups`, and their roles
  (`resource_access.agent-gateway.roles`). The gateway trusts it because of the signature, without asking
  Keycloak each time. It is the reason a change takes up to 5 minutes to show.
- **Device flow**: how the Telegram bot signs someone in without ever seeing a password. The bot shows a short
  code and an address; the person opens it in a browser, signs in to Keycloak, and approves. The bot then holds
  a long-lived *refresh token* (30 days idle) that it swaps for fresh 5-minute tokens. Disabling the account
  makes that swap fail, so the link dies.
- **Account id (`sub`)**: the permanent id of a person. Everything the gateway stores (model key, credentials,
  audit) is filed under it, so renaming a user changes nothing, and deleting-and-recreating an account loses
  their data.
