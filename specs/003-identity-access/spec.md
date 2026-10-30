# Feature Specification: Identity and access

**Feature Branch**: `spec/identity-access`

**Created**: 2026-10-25

**Status**: Draft (all requirements are `planned` in the specs of record; nothing is built)

**Input**: "Replace the gateway's hand-made users, keys and pairing with one identity (the Keycloak
account), a user-management layer inside the agent gateway, and per-source group visibility in the
knowledge base. Telegram first: a person links their Telegram account by signing in to Keycloak, and
the bot then acts as that person. Each person brings their own key for the language model."

The requirements themselves live in the specs of record, each in its own module:

- [`agent-gateway/docs/spec.md`](../../agent-gateway/docs/spec.md): `FR-IDN`, `FR-ATH`, `FR-KEY`,
  `FR-UPS`, `FR-ENT`, `FR-IDP`, `FR-CRD-06..09`, `FR-PRT`, `FR-AUD`, `FR-KBI`, `FR-OPS-05..08`, `FR-CFG-04`.
- [`kb/docs/SPEC.md`](../../kb/docs/SPEC.md): `FR-VIS` (new section 6.12) and amended N3, C4, C6,
  section 11, FR-ENT-10, FR-MCP-04, NFR-CFG-03, Q1.
- The Telegram bot's own spec (`FR-LNK-11..18`, `FR-CMD-09..10`, `FR-CFG-04..05`). That repository
  knows nothing about this stack, so its wording is in
  [`contracts/bot-requirements.md`](contracts/bot-requirements.md) and is applied inside the bot
  repository by unit U8.

This file records the decisions and the stories behind them. It does not repeat the requirement text.

## Where we are today

- The gateway's "users" are free-text names that an operator creates with a command
  (`make gateway-key-create USER=...`). A key belongs to that name. Nobody else knows the name exists.
- Each user's Jira, Confluence and GitLab credentials are set by the operator from the command line.
- The Telegram bot links a Telegram account by an operator approving a pairing code and typing in an
  API key. A person who is linked but has no stored credentials gets an agent with no tools.
- Every call to the language model uses one key shared by the whole deployment, or none.
- Anyone who can call the gateway can use every agent. Every knowledge-base source is visible to
  every caller.

## Clarifications

### Session 2026-10-25 (decisions from the interview, recorded as given)

- Q: How many people, who creates their accounts? → A: Fewer than 200 internal staff. Accounts are
  created by hand in a new Keycloak realm (26.x, self-hosted). No self-registration.
- Q: What is "who a person is"? → A: The Keycloak account id (`sub`). Not an email, not a Telegram
  id, not a name an operator typed.
- Q: Where does user management live? → A: Inside the agent gateway, as its own modular layer, used
  by every client. It replaces the gateway's current users, keys and credentials commands. It is not
  a new service.
- Q: Does Telegram get special treatment? → A: It is the first client built on it and the top
  priority, but the gateway layer is equal scope and must work for any client.
- Q: How does a person get the language model's key? → A: Each person hands their own key for the
  target OpenAI-compatible model to the gateway (portal or command). The gateway uses that key for
  everything it sends to the model on that person's behalf. A deployment may still set one shared
  key as a fallback.
- Q: Does the gateway still issue its own `agk_` keys? → A: Only if the operator turns it on
  (`GATEWAY_ISSUE_KEYS`, default off). With it off, the only credentials are a Keycloak token or the
  person's own registered model key.
- Q: What does the gateway do when Keycloak is down? → A: People already signed in keep working for a
  short time (keep-working-briefly rule: token until it expires, key-bearers until the directory copy
  is 15 minutes old), then are refused.
- Q: Where do groups and roles come from, and who manages them? → A: From the identity provider
  (Keycloak): groups, and roles (the gateway client's own client roles, the place Keycloak keeps
  application roles; best practice is to assign them to groups). The gateway only reads them from
  tokens and from its directory copy. Creating them and assigning them to people happens in
  Keycloak's own admin console, never in the gateway.
- Q: Is Keycloak wired into the gateway? → A: No. The gateway defines a small provider-neutral
  interface (verify a token, list all accounts, run a portal login) and implements only Keycloak
  behind it, chosen by `IDENTITY_PROVIDER=keycloak`. Account management is not part of the interface.
- Q: What decides what a person can do with Jira and Confluence? → A: Those systems' own rules,
  applied through the person's own credentials. The gateway adds only: which agents a person may use
  (by group), whether they may use write tools, and which knowledge-base sources they may see.
- Q: What happens to the existing users, keys and credentials? → A: They are discarded. The current
  users register again, fresh. There is no migration.
- Q: MFA, quotas, automation identities? → A: No MFA now (Keycloak can add it later without changes
  here). Quotas later. Service accounts exist for the knowledge-base scheduler and for automations.
- Q: LibreChat? → A: Lowest priority. It is not touched. It keeps working and sees only knowledge
  sources that have no group restriction.
- Q: Who delivers it? → A: One person with Claude, one to two weeks. Work is on local branches only.

## User Scenarios and Testing

### User Story 1 - Link Telegram by signing in (Priority: P1)

A staff member opens the bot and sends `/login`. The bot replies with a short code and a web address.
They open the address on any device, sign in to Keycloak, enter the code, and approve. The bot tells
them they are linked. From then on every message they send is answered as them: it reaches the
gateway carrying their own identity, so their groups, their stored Jira credentials and their own
model key all apply. No operator does anything.

**Why this priority**: It is the case that failed first (a linked user with no credentials got no
tools) and the one the rest was designed around.

**Independent Test**: Against a throwaway Keycloak, run the device flow with curl, approve it through
the admin API, exchange the code for tokens, and call the gateway with the access token: `/v1/models`
lists exactly the agents that person's groups allow.

**Acceptance Scenarios**:

1. **Given** a person with a Keycloak account who is not linked, **when** they send `/login` in a
   private chat, **then** the bot shows a verification address and a code, and tells them how long
   they have.
2. **Given** a pending login, **when** the person approves it in the browser, **then** within the
   polling interval the bot confirms the link and stores only an encrypted refresh token and the
   person's `sub`.
3. **Given** a linked user, **when** they send a message, **then** the bot obtains a fresh access
   token from the stored refresh token and sends it as the bearer, and the gateway answers as that
   person.
4. **Given** a linked user, **when** they send `/logout`, **then** the bot asks Keycloak to revoke
   the refresh token, deletes the link, and the next message gets the "link first" reply.
5. **Given** a linked user whose Keycloak account is disabled, **when** the bot tries to refresh,
   **then** Keycloak refuses, the bot marks the link expired and tells the user to `/login` again,
   and nothing reaches the gateway.
6. **Given** a pending login, **when** the person does nothing for the code's lifetime, **then** the
   login expires, the bot says so, and offers `/login` again.
7. **Given** a group chat, **when** an unlinked user triggers the bot, **then** no code is posted in
   the group and they are told to message the bot privately.
8. **Given** a linked person with no model key registered, **when** they send a message, **then**
   the gateway's error names the place to register one, and the bot shows that text unchanged.

**Independent of the gateway**: the bot only speaks OpenID Connect. It names no product behind the
issuer.

### User Story 2 - Bring your own model key (Priority: P1)

A person registers their own key for the language model, once, in the portal or with a command. From
then on every model request made for them (the agent loop, the summaries the gateway makes of long
client prompts, and anything passed straight through to the model) is sent with their key. They can
replace or revoke it at any time. The same key also works as a bearer for scripts and for clients
that cannot do the Keycloak sign-in.

**Why this priority**: Without it there is no per-person model usage, and Telegram users have
nothing to attribute or limit by.

**Independent Test**: Register a key for a person, call the gateway with that person's identity,
run a turn against a stub model that records the `Authorization` header it receives, and see the
person's key, not the deployment's.

**Acceptance Scenarios**:

1. **Given** a signed-in person with no model key and no shared fallback, **when** they send a chat,
   **then** the call fails with a clear error naming the portal address where they can register
   their key, and nothing is sent upstream.
2. **Given** any deployment, **when** a person with no key sends a chat, **then** there is no
   shared fallback: the call fails as in scenario 1 (there is no `UPSTREAM_API_KEY`).
3. **Given** a person who registers a key, **when** they register another, **then** the first stops
   working at once and only the second is accepted.
4. **Given** a registered key that only one person registered, **when** it is used as the bearer and
   `GATEWAY_MODEL_KEY_LOGIN` allows it, **then** the gateway recognises the owner by the key's hash,
   builds that person's identity from the directory copy, uses the same key upstream, and (in the
   default `read-only` mode) offers no approval-gated write tool and refuses the raw model pass-through.
5. **Given** a registered key, **when** anyone lists keys, **then** they see a label, a short
   fragment, dates and status and never the key.
6. **Given** a key already registered by someone else, **when** a second person registers the same
   string, **then** it is accepted (colleagues may share a provider key) and nothing tells them
   anyone else holds it. **When** that string is then used as the bearer, **then** it identifies nobody:
   401 `model_key_shared` ("This key is shared by several people, so it cannot identify you. Sign in
   with Keycloak or use a gateway key."). Each person's stored tool credentials stay their own,
   keyed by their Keycloak account, whatever string they present. When all but one holder revokes,
   the remaining holder's string works as a login again; this is expected.
7. **Given** a pass-through request (a model name that is not an agent), **when** the caller has their
   own key, **then** the pass-through uses it, and the caller's token or gateway key is never
   forwarded upstream.

### User Story 3 - Sign in with a Keycloak token, and keep working briefly (Priority: P1)

A client sends a Keycloak access token as the bearer. The gateway checks its signature, issuer,
audience and expiry against keys it fetches from Keycloak and caches, takes the person's groups
from the token, and treats the person as that account. A script that holds only the person's model
key is also accepted; for it the gateway gets the groups and the enabled flag from a copy of
Keycloak's user list that it refreshes every five minutes. If Keycloak cannot be reached, token
callers keep working until their token expires and key callers keep working until the copy is
fifteen minutes old; after that, key callers are refused with a "temporarily unavailable" error.

**Independent Test**: Unit tests with a fake directory and clock: a token with the wrong audience is
refused; a key-bearer is refused when the copy is older than the limit; a disabled person is refused
after the next sync.

**Acceptance Scenarios**:

1. **Given** a valid token for the gateway audience, **when** it is sent, **then** the request runs
   as `sub`, with the token's `groups`.
2. **Given** an expired token, a token for another audience, a token from another issuer, an
   unsigned token or one using an algorithm outside the allowed list, **when** it is sent, **then**
   the answer is 401 in OpenAI's error shape and no work is done.
3. **Given** Keycloak is unreachable and the token's signing key is cached, **when** a token is
   sent, **then** it is still verified from the cache.
4. **Given** a key-bearer whose person is enabled in a copy synced 10 minutes ago, **when** they
   call, **then** they are served.
5. **Given** a key-bearer and a copy older than 15 minutes, **when** they call, **then** they get
   503 with a retry hint, and an audit event records the denial.
6. **Given** a person disabled in Keycloak, **when** the next sync runs, **then** their key and any
   token they still hold are refused, and their refresh at Keycloak already fails.
7. **Given** a person removed from a group in Keycloak, **when** a key-bearer calls after the next
   sync, **then** the removal applies; a token carries the old groups until it expires (at most the
   access-token lifetime, 5 minutes).
8. **Given** a service account's token, **when** it is sent, **then** the caller is a `service`
   principal, with the groups assigned to that account.
9. **Given** the issuer or the directory service account is not configured, **when** the gateway
   starts, **then** it refuses to start and names the missing setting.

### User Story 4 - Choose who may use which agent (Priority: P2)

An operator writes, in an agent's file, which groups or roles may use it (a caller matches if it has
any listed group or any listed role), and optionally which groups or roles may use its write tools. People outside the groups do not see the agent in the model list,
cannot call it by name, and are not offered it as a handoff. People who may use it but not write see
it without its write tools.

**Independent Test**: Agent files with `access` blocks, three principals with different groups,
asserted against `/v1/models`, direct routing, handoff offers and the tool list the model receives.

**Acceptance Scenarios**:

1. **Given** an agent with no `access` block, **when** any signed-in person lists models, **then**
   they see it.
2. **Given** an agent restricted to group `product`, **when** a person outside that group lists
   models, **then** it is absent; **when** they request it by name, **then** the answer is the same
   "model not found" as for a name that does not exist.
3. **Given** an agent with `write_groups: [leads]`, **when** a member of `product` but not `leads`
   calls it, **then** the model is not offered any approval-gated write tool, so there is nothing
   to approve.
4. **Given** a key with `write: false` in its scope, **when** its holder calls an agent, **then**
   write tools are hidden whatever their groups.
5. **Given** a key whose scope names agents, **when** its holder lists models, **then** only the
   intersection of those agents and their permitted agents appears.
6. **Given** a conversation that handed off to a specialist, **when** the person is later removed
   from the specialist's group, **then** the next turn does not continue with the specialist.
7. **Given** a paused turn awaiting approval, **when** the person's group membership changes before
   they answer, **then** the resume re-checks and refuses what they may no longer do.
8. **Given** a model name that is not an agent, **when** any signed-in person sends it, **then**
   access rules do not apply to it (the pass-through is governed by the model key, not by groups).
9. **Given** an agent with `roles: [analyst]` and `groups: [product]`, **when** a person with the
   role `analyst` but no group, and another with the group but no role, each list models, **then**
   both see it; a person with neither does not.
10. **Given** a role assigned to a person in Keycloak, **when** they next get a token (or, for a key
    caller, after the next sync), **then** the agent appears; the gateway has no way to grant it.

### User Story 5 - A small page for my own settings (Priority: P2)

A person opens `/portal` on the gateway, signs in with Keycloak, and sees two things: their model key
(register, replace, revoke; never shown back) and their tool credentials, one card per tool server
that needs them, listing which variables are set (never the values), with set, change and delete.
If the operator enabled gateway-issued keys, a third card creates and revokes those. A "test" button
checks a credential without revealing it.

**Independent Test**: HTTP tests with a fake identity provider: sign in, post a credential, get the
page again and find only the variable name; post without the CSRF token and be refused.

**Acceptance Scenarios**:

1. **Given** a signed-out visitor, **when** they open any portal page, **then** they are redirected
   to Keycloak and return signed in.
2. **Given** a signed-in person, **when** they set `JIRA_PASSWORD`, **then** the page afterwards says
   "set" and never contains the value; the value is stored encrypted under their `sub`.
3. **Given** a form post without the page's CSRF token, or from a different origin, **when** it
   arrives, **then** it is refused and nothing changes.
4. **Given** a server that declares which variables it needs, **when** the credentials card is
   built, **then** only declared variables appear, with their labels, and undeclared ones are
   refused.
5. **Given** the issued-keys flag is off, **when** a person looks for it, **then** there is no such
   card, and a hand-made request to create one is refused.
6. **Given** a person disabled in Keycloak, **when** they use an existing portal session, **then**
   they are signed out at the next request after the next sync.

### User Story 6 - Knowledge sources visible by group (Priority: P2)

An operator writes `groups: [engineering]` under a source in `sources.yaml`. After the next scheduler
run, only callers who are members of one of those groups can find or read that source's entries
(including any stored text). A source with no `groups` stays visible to everyone, including callers
that supply no group information, such as LibreChat.

**Independent Test**: Fake catalog with two sources, one restricted; search and get with no groups,
with matching groups and with other groups; plus a header test for the trust rule.

**Acceptance Scenarios**:

1. **Given** an unrestricted source and a restricted one, **when** a caller with no group
   information searches, **then** only the unrestricted source's entries come back.
2. **Given** the same catalog, **when** a caller whose groups include one of the restricted source's
   groups searches, **then** both appear.
3. **Given** a restricted entry's id, **when** an unauthorised caller asks for it, **then** the
   answer is identical to asking for an id that does not exist.
4. **Given** a request that carries `X-KB-Groups` but a wrong or missing `X-KB-Trust`, **when** it
   reaches the catalog server, **then** the groups are ignored and the caller is treated as having
   none.
5. **Given** a restricted source with stored text, **when** an unauthorised caller reads it, **then**
   it is refused exactly like the entry.
6. **Given** `groups` is added to a source that was unrestricted, **when** the scheduler next runs,
   **then** the restriction applies at once for that source, without a re-summary.
7. **Given** a new source, **when** the scheduler writes its first entries, **then** its visibility
   is recorded first, so there is no moment when the entries are visible by accident.
8. **Given** the gateway calls the catalog for a signed-in person, **when** the server is flagged
   `forward_identity`, **then** the person's groups and the shared secret are sent, and for any
   other server they are not.

### User Story 7 - See what happened (Priority: P3)

An operator lists recent audit events: who registered or revoked a key, who set or deleted a stored
credential, and which requests were refused and why. No event contains a secret.

**Acceptance Scenarios**:

1. **Given** a person revoking their model key in the portal, **when** the operator lists events,
   **then** there is an entry with the actor, the action `key.revoke`, the key id and the time.
2. **Given** a refused request (expired token, disabled person, stale directory), **when** the
   operator lists events, **then** an entry names the reason and never contains the token.
3. **Given** a flood of refused requests with the same reason, **when** events are recorded, **then**
   repeats within a minute are counted into one entry, so the log cannot be used to fill the disk.
4. **Given** an event older than the retention period, **when** pruning runs, **then** it is gone.

### User Story 8 - Issued keys and service identities (Priority: P3)

If the operator turned `GATEWAY_ISSUE_KEYS` on, a person (portal) or operator (command) can mint an
`agk_` key owned by that person, with an expiry and a scope (which agents, read-only or not). The
knowledge-base scheduler and automations use a Keycloak service account (client credentials), or a
key issued to a person who delegates it.

**Acceptance Scenarios**:

1. **Given** the flag is off, **when** anyone tries to create an issued key through the command or
   portal, **then** it is refused with a message naming the flag.
2. **Given** the flag is on, **when** a key is issued, **then** the secret is shown once and only its
   hash is kept.
3. **Given** the flag is turned off while issued keys exist, **when** one of them is used, **then**
   it no longer authenticates.
4. **Given** an issued key past its expiry or revoked, **when** it is used, **then** it is refused on
   that request.

### User Story 10 - Swap the identity provider later (Priority: P3)

A developer who must move to a different OpenID Connect provider writes one adapter that implements
three small ports and runs the shared provider contract tests against it. They set
`IDENTITY_PROVIDER` to the new name. No gateway core code, portal page or other adapter changes.

**Acceptance Scenarios**:

1. **Given** the Keycloak adapter, **when** the provider contract suite runs, **then** it passes
   with no live service.
2. **Given** an unknown `IDENTITY_PROVIDER`, **when** the gateway starts, **then** it stops and names
   the allowed values.
3. **Given** the gateway source, **when** searched outside `adapters/outbound/identity/keycloak/`
   (and docs and tests of that adapter), **then** no Keycloak wording or Keycloak setting is read.

### User Story 9 - Provision the realm (Priority: P3)

An operator imports one realm template and runs one idempotent script. They get the clients the
gateway, portal, Telegram bot, directory sync and automations need, the groups claim, and the
audience, with nothing else turned on.

**Acceptance Scenarios**:

1. **Given** an empty Keycloak, **when** the apply script runs, **then** the realm and its clients
   exist; **when** it runs again, **then** nothing changes.
2. **Given** a token from the Telegram client, **when** inspected, **then** it carries the gateway
   audience and the person's group names without paths.
3. **Given** the directory service account, **when** it lists users and groups, **then** it can read
   them and nothing else.

## Edge Cases

- Group names that contain a comma cannot travel in `X-KB-Groups`. They are rejected in a source's
  `groups` and dropped (with a log line) by the gateway when forwarding.
- Group names are compared exactly, case-sensitively, with no path. Nested groups give no
  inheritance: a member of a child group is in the child only.
- A token and a key are told apart safely: the gateway looks the bearer up as a key first (one
  indexed lookup of its hash), and only then tries it as a token. A model key that happens to look
  like a token still works.
- A key registered for one person whose Keycloak account is deleted: the key stops authenticating at
  once (no directory entry). Their stored data stays until an operator purges the owner.
- A person with several Telegram accounts may link each one. Each link holds its own refresh token.
- The access token's `groups` claim can be large for a person in many groups; the gateway rejects a
  bearer larger than 8 KiB.
- Clock skew: tokens are accepted up to 60 seconds either side of `exp` and `nbf`.
- Rotation: when Keycloak rotates its signing keys, an unknown `kid` triggers one JWKS refetch, at
  most once per 30 seconds, before the token is refused.
- The directory sync pages through all users; if one page fails the whole sync is discarded and the
  previous copy stays (a half copy would wrongly delete people).
- Two portal tabs, a restart of the gateway, or two gateway processes: portal sessions are signed
  cookies, so they survive restarts and need no shared store.
- A key-bearer's person is unknown to the directory copy because they were created a minute ago: the
  gateway refreshes the copy on demand, at most once every 30 seconds, and then decides.
- A deployment moved onto this change loses all old threads, pending approvals, stored credentials
  and keys (ids change to `sub`). People register again. This is intended.
- An `offline_access` refresh token is long-lived and sits in the bot's database, encrypted. If the
  bot's encryption key leaks together with its database, the holder can act as every linked person
  until the tokens are revoked. `/logout` revokes; so does disabling the account; an operator can
  revoke all of a user's sessions in Keycloak.
- LibreChat sends no group information, so it sees only unrestricted sources, whoever the end user is.
- A role that exists in Keycloak but is named in no agent file has no effect. A role renamed in
  Keycloak silently stops matching; `check` warns about agent-file names found in no directory row.
- Knowledge-base visibility uses groups only. A role cannot open a restricted source.

## Out of scope

- Touching LibreChat, its login, or its agents.
- MFA, password policy, self-registration, email verification (Keycloak's job, later).
- Quotas and spend limits (the data to enforce them, per-person keys and audit, is created here).
- Atlassian OAuth. Jira, Confluence and GitLab keep personal credentials.
- A web UI for administrators in the portal. Administration stays in the command line.
- Inheriting groups from parent groups; per-entry or per-user access in the knowledge base.
- Migrating existing users, keys or credentials.

## Requirements

Full text and status are in the specs of record. Plain-language map:

| Spec of record | Meaning |
|---|---|
| gateway FR-IDN-01..08 | one identity (`sub`), the principal, the directory copy and its refresh, service principals, old data dropped |
| gateway FR-ATH-01..14 | how a bearer is resolved (incl. `GATEWAY_MODEL_KEY_LOGIN` and the shared-key refusal); token checks; JWKS cache; key callers use the directory copy; keep-working-briefly; disabled people refused; startup check |
| gateway FR-KEY-01..08 | registered model keys (hashed and encrypted), one active per person, the same string allowed for several people, issued keys only under the flag, listing without secrets, revoke, last used |
| gateway FR-UPS-01..08 | every model call uses the caller's key; no shared fallback; the pass-through substitutes the key; provider rejections explained; credentials keyed by account only |
| gateway FR-RTE-07..09 | pass-through path confinement, key scope, header allow-list |
| gateway FR-ENT-01..10 | agent `access` by groups or roles and write rule likewise, models list, routing, handoffs, resume, write-tool hiding, key scope |
| gateway FR-IDP-01..07 | the provider-neutral identity interface, Keycloak as the only adapter, read-only toward the provider, shared provider contract tests |
| gateway FR-CRD-06..09 | list credential names, delete one variable, declared credentials with labels, keyed by `sub` |
| gateway FR-PRT-01..13 | the portal: sign-in, cookie session, CSRF, key and credential pages, no secrets in pages, audit |
| gateway FR-AUD-01..06 | audit table, events, no secrets, listing, repeats collapsed, retention |
| gateway FR-KBI-01..04 | forwarding groups and the trust secret to servers flagged `forward_identity` |
| gateway FR-OPS-05..08 | commands for keys, credentials, audit, directory, owner purge |
| gateway FR-CFG-04 | the new settings and their startup validation |
| kb FR-VIS-01..12 | source groups, visibility table, filtering search and get, trust header, fail closed |
| bot FR-LNK-11..18, FR-CMD-09..10, FR-CFG-04..05 | device sign-in link mode, refresh per turn, `/login`, `/logout`, revocation, renewal |

## Success Criteria

- **SC-001**: A new person links Telegram and gets an answer in under two minutes, with no operator
  step, measured in the end-to-end recipe in [`quickstart.md`](quickstart.md).
- **SC-002**: After a person is disabled in Keycloak, no request as them is served later than one
  directory sync interval plus one access-token lifetime (10 minutes with defaults), and no new
  access token can be obtained at all.
- **SC-003**: With Keycloak down for 10 minutes, token callers and key callers are served as before;
  at 16 minutes key callers get a 503 and token callers whose token has expired get a 401.
- **SC-004**: A sentinel secret placed in a model key, a stored credential and a request header never
  appears in any response body, log line, audit row or error in the test suites.
- **SC-005**: A caller with no group information can retrieve zero entries of any restricted source
  by search, by id, or by reading stored text; the three answers equal those for a missing id.
- **SC-006**: In the end-to-end run against a stub model, the key the stub receives is the caller's
  own in every one of: an agent turn, a compaction call, a pass-through request and a model listing.
- **SC-007**: With `GATEWAY_ISSUE_KEYS` unset, no `agk_` key can be created by any route.
- **SC-008**: LibreChat's existing flows, run unchanged, still work: the knowledge base returns all
  unrestricted sources and nothing else changes for it.
- **SC-009**: The unit suites of `agent-gateway`, `kb` and the bot run with no services and stay
  green, including the spec-coverage and boundary gates.
