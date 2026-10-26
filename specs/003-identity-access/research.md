# Research: Identity and access

Decisions, what was rejected, and what is still unproven. "Interview" means the decisions recorded
in `spec.md`.

## Decisions

### D1. Identity is the Keycloak `sub`, everywhere

- **Decision**: Every owner column holds the Keycloak account id. The gateway never invents users.
- **Why**: A name an operator types can collide, drift from the real account, or be shared. `sub` is
  stable and not reusable. It lets disabling an account in one place cut access everywhere.
- **Rejected**: email or username as the key (they change); the Telegram id as identity (it proves
  nothing about who the person is at work); keeping free-text users and mapping them to accounts
  (two sources of truth).

### D2. Keycloak-only vs. our own access service

- **Decision**: Keycloak holds accounts, groups and sign-in. The gateway adds a thin layer: a
  directory copy, keys the person owns, stored tool credentials, audit and a portal. No new service.
- **Why**: Under 200 people, one operator and one to two weeks. The things Keycloak cannot do for us
  (hold a person's Jira password, the model key, an audit of those, per-agent rules) are a few
  tables and pages inside the module that already needs them.
- **Rejected**:
  - *Keycloak alone*: it has no place for per-person tool credentials or model keys, and the gateway
    would still need its own ownership model.
  - *A new "access" microservice* (keys, credentials, links, audit behind its own API; an earlier
    direction in project notes): more to deploy, secure and back up for the same few tables. It
    remains a valid later extraction, since the work is behind ports; the ports are the seam.
  - *Using Keycloak user attributes to store credentials*: puts secrets in the identity provider's
    database and its admin API surface.

### D3. Two bearer kinds, key first

- **Decision**: Look the bearer up as a key (hash) before trying it as a token.
- **Why**: It is one indexed read; a token is never a valid key by accident (a hash of a random
  token matches nothing); and a model key that looks like a token still works. Verifying a token
  first would spend a signature check on every key.
- **Rejected**: telling them apart by shape (`eyJ...`, two dots): a model key may legally have that
  shape.

### D4. The model key is the person's own, stored reversibly, plus a hash

- **Decision**: Fernet encryption with the existing `CREDS_KEY` (the gateway must send the key, so
  one-way is impossible), plus SHA-256 of the key for recognition.
- **Why**: Per-person upstream use and a usable bearer for scripts. Same mechanism and the same
  loss-of-key consequence as stored Jira credentials, so no new operational rule.
- **Resolved ambiguity**: the original plan also described the model key as "one more credential
  entry (`server=llm`)". Two copies of one secret would drift, so there is one copy in `api_keys` and
  `llm/API_KEY` is only how it is presented by the portal and `creds list` (data model, section 5).
- **Rejected**: a single shared key (no attribution, no revocation of one person); asking the model
  endpoint to authenticate Keycloak tokens (the endpoint is a third-party-style service we do not
  configure).

### D5. Gateway-issued keys stay, behind a flag, default off

- **Decision**: `GATEWAY_ISSUE_KEYS=false` by default. On, the old behaviour (key owned by a person,
  scope, expiry) works.
- **Why**: Some clients cannot do the sign-in and cannot hold a model key of their own; the flag
  lets the operator choose per deployment rather than per code change. Off by default removes a
  credential type nobody asked for.
- **Rejected**: removing issued keys entirely (strands scripted clients); always on (a second way to
  hold a long-lived bearer without need).

### D6. "Keep working briefly"

- **Decision**: Token callers work until `exp` (5 minutes) even if Keycloak is down; signing keys
  stay usable from cache if the fetch fails (stale-if-error). Key callers work while the directory
  copy is at most 15 minutes old (three missed syncs), then get 503, not 401.
- **Why**: A restart or a short outage of Keycloak should not log everyone out of a chat bot; but an
  open-ended grace would mean a disabled account keeps working through an outage forever. 503 tells
  a client "retry" rather than "your credential is wrong".
- **Tightening beyond the interview**: a token for a person whose fresh directory row says
  `enabled=false` is refused (FR-ATH-07). The interview says "disabled user → denied after next
  sync"; without this, a disabled person's token would work until it expires. A missing row does
  not refuse a token (a brand-new account is not yet synced).
- **Rejected**: asking Keycloak for every request (availability coupling); unlimited grace.

### D7. Groups come from the token or the copy, flat and by name

- **Decision**: leaf group names, exact, no inheritance (`full.path=false`).
- **Why**: Simplest rule to explain to the operator and to test; paths with slashes complicate
  headers and YAML. With under 200 people a flat set of groups is enough.
- **Rejected**: roles instead of groups (groups are what operators manage per team); nested
  inheritance (surprising grants); group ids (unreadable in YAML).

### D14. Roles as well as groups, read-only, groups-only for the knowledge base

- **Decision**: The provider supplies both groups and roles. For Keycloak, following its own
  conventions and with no custom mapping: roles are the **client roles of the gateway's client**,
  read from the standard `resource_access.<client>.roles` token claim and, in the directory sync,
  from the Admin API's effective client-role mappings (which include roles inherited through groups
  and composites); realm roles are cross-application and not used; groups come from the standard
  Group Membership mapper (`groups`, `full.path=false`). Administrators assign client roles to
  groups, not people. The port's `roles` field stays provider-neutral. An agent's `access` block accepts
  `groups`, `roles`, `write_groups` and `write_roles`; a caller is allowed on any match (OR). The
  gateway only reads them. Creating and assigning stay in Keycloak's admin UI.
- **Why**: Groups say who someone works with; roles say what they may do. Operators already use both
  in Keycloak, and an agent rule such as "analysts or the product group" would otherwise need an
  invented group.
- **Why OR and not AND**: it keeps the rule a plain list that is easy to read and review, and a
  stricter combination can be had by creating one group or role that means it.
- **Knowledge base stays groups-only**: a source is a place, and groups are how people are organised
  around places; roles describe capabilities of the person in a tool. Header size and the trust
  header contract stay as designed (`X-KB-Groups` only). Adding roles later is an additive change
  (a `roles` column and an `X-KB-Roles` header) with nothing here blocking it. No reason to widen it
  now: nobody asked, and every added dimension is one more way to expose a restricted source by
  mistake.
- **Rejected**: roles only (loses team-shaped access); AND semantics; letting the gateway assign
  roles (the gateway would become an account-management system and need write rights at Keycloak).

### D15. A provider-neutral identity interface, one implementation

- **Decision**: `application/identity_ports.py` defines `TokenVerifier.verify -> Identity`,
  `DirectorySource.fetch_all -> list[Account]` and `PortalLogin` (authorize URL, complete). Everything
  Keycloak-specific (JWKS, admin REST paths, the `resource_access` claim shape, client secrets) lives in
  `adapters/outbound/identity/keycloak/`. `IDENTITY_PROVIDER=keycloak` selects it. A shared contract
  test class defines what any adapter must do.
- **Why**: The project's own constitution wants the core free of vendor detail, a second provider is
  plausible (the organisation may change its identity system), and the seam costs three small
  protocols and one test file. It also keeps the sync job, the authenticator and the portal testable
  with fakes.
- **Out of the interface**: account management (create users, assign roles, passwords, MFA). Those
  are exactly the things that differ most between providers and that an admin UI already does well;
  having the gateway call them would require write rights and a second place to administer people.
- **Rejected**: a generic OIDC adapter only (admin listing is not standardised: SCIM or vendor APIs
  are needed for the directory); a plug-in loading mechanism by entry points (one implementation;
  the composition root can import it directly); building more than one provider now (nothing to
  test it against).

### D8. Agent access lives in the agent file

- **Decision**: `access.groups` / `access.write_groups` in the agent YAML; evaluated per request from
  the principal.
- **Why**: Declarative state (constitution): the file is the truth for what it lists, reviewable in a
  diff. Not stored in threads, so a group change applies at the next turn.
- **Rejected**: a table managed by commands (state with no review trail); per-tool rules (the
  approval gate already covers writes; the group for it is the only extra needed).

### D9. A forbidden agent is "not found"

- **Decision**: 404 `model_not_found`, identical to an unknown model.
- **Why**: No information about which agents exist for people who cannot use them.
- **Rejected**: 403 (confirms existence).

### D10. Portal: server-rendered, stateless cookie, same app

- **Decision**: Jinja pages, a signed cookie, CSRF token plus origin check, mounted in the gateway.
- **Why**: Four pages; a SPA and a build step are not worth it. A signed cookie needs no table and
  survives restarts. The pattern is already used by the kb admin console
  (`kb/src/kb/adapters/inbound/admin/auth.py`: derived key, HMAC, origin check).
- **Rejected**: a separate portal service (another port to secure); server-side session table (state
  to prune for no benefit at this size); storing the Keycloak tokens in the session (the portal does
  not need to call anything as the person).

### D11. KB: group visibility per source, fail closed, shared secret for the header

- **Decision**: `groups` on a source; `source_visibility` table; the catalog filters in the query;
  `mcp-kb` honours `X-KB-Groups` only with `X-KB-Trust` equal to `KB_TRUST_SECRET`.
- **Why**: `mcp-kb` is on `backend`, reachable by LibreChat and anything else there, and MCP's HTTP
  transport has no authentication. Without a secret any container could claim any group. LibreChat
  does not have the secret and sends no groups, so it sees unrestricted sources only, which is
  today's behaviour for a deployment with no restricted sources.
- **Fail closed**: a source with no visibility row is hidden. The migration backfills existing
  sources so that day-one behaviour is unchanged.
- **Rejected**: per-entry ACLs (the source is the unit an operator already reasons about; an entry
  inherits its source's access); filtering in the gateway after the fact (the catalog would still
  return restricted entries to anything that reaches it, and ranking and limits would leak counts);
  using the entry's `audience` field (it is per entry and was never enforced; it stays
  informational).

### D12. No migration of existing data

- **Decision**: Drop old keys, credentials, threads, pending turns, responses.
- **Why**: The interview says current users register fresh; old ids cannot be mapped to accounts;
  keeping orphaned rows would hold encrypted secrets for nobody.

### D13. The bot stays generic

- **Decision**: The device sign-in is implemented against any OpenID Connect issuer; the bot's spec
  text lives here (`contracts/bot-requirements.md`) until U8 applies it inside that repository.
- **Why**: The bot's own constitution forbids naming the stack; it is a separate repository.

## Alternatives for the Telegram link (rejected)

| Alternative | Why not |
|---|---|
| Keep operator-approved pairing, but issue the key from the portal | The operator is still in the loop for every user; the person must handle a long-lived key |
| Telegram Login Widget / Mini App with the issuer's authorization code flow | Needs a public redirect URL on the bot and a web page; the device grant needs neither |
| User types their password into the chat | Never |
| Direct access grant (password grant) | Same problem; disabled in the realm |
| A link token generated in the portal and pasted into the bot | Workable, but bespoke; the device grant is a standard, with expiry and polling built in |

## Risks and open questions

| id | Risk | Likelihood / impact | Handling |
|---|---|---|---|
| R1 | **`offline_access` through the device grant.** ~~Keycloak may or may not issue an offline refresh token.~~ **Verified on Keycloak 26.8.1: it does** (see "Verified against Keycloak 26.8.1"). | Resolved / low. | The `telegram-bot` client lists `offline_access` as an optional scope; the bot requests `openid offline_access`; the fallback (raise the client's own session lifespans) is not needed and not applied. |
| R2 | **Security of long-lived refresh tokens at rest.** An offline token is valid for up to 30 idle days and sits in the bot's database. A database copy plus the bot's `CREDS_KEY` is enough to act as every linked person until revoked. | Low / high. | Encrypted with a key held outside the database; refresh-token rotation turns on in the realm so a stolen token that was already used reveals itself; `/logout` and account disabling revoke; the operator can list and unlink; realm idle timeout is configurable; document that `CREDS_KEY` and the database must not be backed up together unprotected. |
| R3 | **Disabled person's access token stays valid until `exp`.** Assumed yes (5 minutes). | Certain / low. | Bounded by the access-token lifetime; the directory row check (FR-ATH-07) shortens it to one sync interval. |
| R4 | **LibreChat sends no group header.** It sees only unrestricted sources, whoever the user is. | Certain / low (LibreChat is lowest priority). | Stated in `kb/docs/SPEC.md` (Q1 closed) and the operator guide. |
| R5 | **`mcp-kb` is reachable from `backend`.** Anything there could send headers. | Medium / medium. | The trust secret; absence of the secret means groups are ignored. A leaked secret lets a container on `backend` claim groups, so it is treated like any other secret in `.env`. |
| R6 | **Thread continuity lost when ids move to `sub`.** | Certain / low. | Intended (D12); documented in the runbook. |
| R7 | **Directory sync correctness.** A partial list would delete people. | Low / high. | Whole-sync atomicity (data model, section 6); a failed page discards the sync. |
| R8 | **Group claim size.** A person in very many groups may exceed header limits. | Low / low. | 8 KiB bearer limit; 64-group and 4 KiB limits on `X-KB-Groups` (truncated, with a log line). |
| R9 | **Clock skew** between Keycloak and the gateway. | Low / low. | 60 s leeway. |
| R10 | **Keycloak admin API shape differences across 26.x minor versions** (users and group members endpoints). | Low / medium. | The directory adapter is the only code that knows; the end-to-end run uses the pinned image; contract tests against recorded responses in U2. |
| R12 | ~~Two role mappers into one claim.~~ **Resolved by following Keycloak's default**: no custom role mapper; the adapter reads only `resource_access.<client>.roles`. Remaining check: with `fullScopeAllowed` off, the client scope mappings must let the gateway client's roles into each signing-in client's tokens (quickstart step 3 decodes a token to confirm). | Resolved / low. | Scope mappings in the realm template. |
| R13 | **Role names drift.** A role renamed or deleted in Keycloak silently stops matching an agent. | Medium / low. | `check` warns about names found in no directory row; roles in the directory copy are listed by `directory show`. |
| R11 | **Delivery size.** Nine units in one to two weeks by one person and Claude. | Medium / medium. | The units are independent behind ports; waves allow dropping U4 (portal) to a later pass without breaking the rest, since the CLI covers key and credential management (FR-OPS-05..06). |

## Things we checked and decided not to build

- Rate limits per person beyond today's per-key limit: the limit now keys on the person (FR-ATH-10);
  quotas are later.
- Revocation lists for access tokens: the 5-minute lifetime and the directory check are enough for
  the stated threat.
- Self-service group requests, approvals, or SCIM: out of scope.

## Verified against Keycloak 26.8.1

Run by `scripts/identity-e2e.sh` (U9) against `quay.io/keycloak/keycloak:26.8.1 start-dev` with the realm
applied by `integrations/keycloak/apply.sh`. 36 checks pass; the gateway steps are skipped when the
gateway does not accept Keycloak tokens yet.

- **R1, `offline_access` through the device grant: yes.** With `scope=openid offline_access` the token
  response carries `scope: "openid profile offline_access"`, `refresh_expires_in: 2592000` (30 days, the
  realm's `offlineSessionIdleTimeout`) and a refresh token whose `typ` claim is `Offline`. The default role
  `offline_access` is enough; no per-client setting beyond listing the scope as optional. The user is shown a
  consent page ("Grant access") after signing in because `offline_access` is requested; the approval is one
  click and the final page reads "Device Login Successful".
- **Refresh-token rotation works as the contract says**: a refresh returns a new refresh token, and the
  already used one is refused with `invalid_grant`. The bot must store the new one (FR-LNK-14).
- **Disabled person**: a disabled account cannot complete the device sign-in, and a refresh token issued
  before the account was disabled returns `invalid_grant` immediately. The access token already issued stays
  valid until it expires (5 minutes), as assumed in R3.
- **`resource_access.agent-gateway.roles` appears through group-assigned client roles.** Alice (groups
  `product`, `leads`; no role assigned to her directly) gets `["agent-user","agent-writer"]`, Bob (group
  `engineering`) gets `["agent-user"]`. The `groups` claim is `["leads","product"]`: leaf names, no slash.
  The Admin API's effective endpoint (`.../role-mappings/clients/{uuid}/composite`) returns the same set, so
  the token and the directory copy agree.
- **Token shape differences the adapter must tolerate**: `aud` is a plain string (`"agent-gateway"`) when it
  is the only audience, not a one-element list; the `account` audience seen in the contract's example does not
  appear with `fullScopeAllowed` off. The access token also carries `realm_access.roles: ["offline_access"]`
  (from the built-in `roles` scope); the adapter ignores realm roles, as specified.
- **Things the realm needs that the contract did not spell out** (all in the template):
  - The built-in scope `basic` must stay attached to each signing-in client: it is what puts `sub` in the token.
  - The directory service account needs a client scope mapping for the `realm-management` roles it holds in
    addition to the roles themselves, because `fullScopeAllowed` is off. Without it every Admin API call is
    403 even though the account has `view-users`. The apply script grants exactly `view-users`, `query-users`,
    `query-groups`, `view-clients` and removes any other `realm-management` role from that account on rerun.
    With that, listing users, finding the client and reading effective roles and groups work (200), and
    creating a group or a user is refused (403).
  - A realm import (`--import-realm`) that lists `clientScopes` suppresses Keycloak's built-in scopes
    (`profile`, `roles`, `basic`, `offline_access`), so the template is applied through the Admin API
    instead (`apply.sh`), which starts from a realm created with the built-ins.
- **Idempotence**: a second `apply.sh` run creates nothing and changes nothing the operator owns. Groups,
  the roles on a group, and the people in them are seeded once and never rewritten.
