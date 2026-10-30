# Contract: Telegram bot requirements (generic wording)

For unit U8, to be applied **inside the bot repository** (`telegram-bot-gateway/`, a git submodule).
That repository knows nothing about the stack that uses it: the text below names no other product, no
gateway and no identity provider by name, only "an OpenID Connect issuer". Do not add references to
anything outside the bot when applying it. This file lives here only because the bot's own repository
must not be edited from this change.

Apply as follows in the bot's `docs/spec.md`; every new row is `planned` until built and claimed by a
test (`# Covers: ...`). The bot's own spec-coverage test enforces that.

## 1. Glossary additions (section 2)

| Term | Meaning |
|---|---|
| **Issuer** | An OpenID Connect provider the bot signs users in with (`OIDC_ISSUER`). |
| **Device sign-in** | The OAuth 2.0 device authorization grant (RFC 8628): the bot shows a code and an address, the user approves on another screen. |

## 2. Decisions of record (section 3): add rows

| Decision | Reason |
|---|---|
| A second link mode, device sign-in, next to pairing | The operator approving a pairing code does not scale and ties a Telegram account to an API key someone must hand over. An issuer's approval is the user's own. |
| Only a refresh token is stored, never a password or a long-lived access token | A refresh token can be revoked at the issuer; a password cannot be un-shared. |
| The bot calls the endpoint with a fresh access token on every turn | The endpoint then sees who the person is and when they were disabled, without the bot caching an identity. |

## 3. Requirements (section 5.1 Linking, `FR-LNK`)

| id | Requirement | Status |
|---|---|---|
| FR-LNK-11 | With `LINK_MODE=device`, `/login` in a private chat MUST start a device authorization at the issuer and show the verification address (the complete one when the issuer offers it), the user code, and how many minutes remain. In a group it MUST NOT show a code and MUST ask the user to message the bot privately. | planned |
| FR-LNK-12 | The bot MUST poll the issuer's token endpoint at the interval it was given, add 5 seconds on `slow_down`, and stop on success, `access_denied`, `expired_token` or the code's expiry, telling the user which. One pending login per user: a new `/login` replaces it. A pending login MUST survive a restart. | planned |
| FR-LNK-13 | On success the bot MUST store the user's refresh token encrypted with a key from the environment, the issuer's subject (`sub`) and a display name, and MUST NOT store a password or a long-lived access token. | planned |
| FR-LNK-14 | For each turn of a device-linked user the bot MUST send the endpoint a valid access token as the bearer, obtaining one by the refresh grant when it has none that is valid for at least another 60 seconds. If the issuer returns a new refresh token it MUST be stored before the access token is used. Concurrent turns of one user MUST NOT both refresh. | planned |
| FR-LNK-15 | When the issuer refuses a refresh (`invalid_grant`, `unauthorized_client`), the bot MUST mark the link expired, clear the stored refresh token, tell the user to `/login` again, and send nothing to the endpoint. A transient failure (network error, 5xx) MUST produce a "try again" reply and leave the link as it is. | planned |
| FR-LNK-16 | `/logout` MUST revoke the refresh token at the issuer's revocation endpoint when it advertises one (RFC 7009), then delete the link whether or not revocation succeeded, and say which happened. | planned |
| FR-LNK-17 | `LINK_MODE=pairing` (the default) MUST behave exactly as before. In device mode an unlinked user's private message MUST get an invitation to `/login` and no pairing code; links made by pairing MUST keep working; `/login` by a pairing-linked user MUST replace that link. | planned |
| FR-LNK-18 | Endpoints MUST come from the issuer's discovery document (`/.well-known/openid-configuration`, cached for one hour). Claims of tokens received directly from the token endpoint over TLS MAY be read without checking their signature; `sub` MUST be present or the login fails. Refresh tokens, device codes and access tokens MUST NOT appear in a log, a reply or an error. | planned |

Amend existing rows' wording:

- **FR-LNK-06**: "The operator MUST be able to unlink by Telegram id or username ..." gains: "... for
  either kind of link; the CLI listing shows each link's kind (`api_key` or `oidc`) and status."
- **FR-LNK-01**, **FR-LNK-10**: unchanged; an `expired` link is treated like no link (FR-LNK-01), and
  FR-LNK-10's renewal message is the one shown for an undecryptable refresh token as well.

## 4. Commands (section 5.7, `FR-CMD`)

| id | Requirement | Status |
|---|---|---|
| FR-CMD-09 | In device mode the bot MUST support `/login`, registered in the command menu; in pairing mode it MUST NOT list it. | planned |
| FR-CMD-10 | In device mode the bot MUST support `/logout`, registered in the command menu. | planned |

Amend **FR-CMD-08**: "`/whoami` ... MUST also show the link kind, and for a device link the issuer's
subject and the link's status, and no secret."

## 5. Configuration (section 5.9, `FR-CFG`)

| id | Requirement | Status |
|---|---|---|
| FR-CFG-04 | Variables for device mode: `LINK_MODE` (`pairing` default, or `device`), `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` (optional; for a confidential client), `OIDC_SCOPE` (default `openid offline_access`). `OIDC_ISSUER` and `OIDC_CLIENT_ID` are required when `LINK_MODE=device`. | planned |
| FR-CFG-05 | In device mode the bot MUST fail to start if the issuer's discovery document has no device authorization endpoint; an issuer that cannot be reached at start MUST give a warning, and `/login` MUST then tell the user the sign-in service is unavailable. `OPENAI_API_KEY` is optional in device mode. | planned |

## 6. Acceptance scenarios (section 4)

| id | Scenario | Status |
|---|---|---|
| AS-06 | **Given** device mode and an unlinked user, **when** they send `/login` and approve at the issuer, **then** their next message is answered by the endpoint with a bearer token that identifies them. | planned |
| AS-07 | **Given** a device-linked user whose account the issuer has disabled, **when** they message the bot, **then** they are told to sign in again and nothing reaches the endpoint. | planned |

## 7. Section 6 (out of scope): change one row

Replace the row "Keycloak linking | Planned as a second adapter of FR-LNK-08." with:
"Password sign-in, and any sign-in other than the device grant | The bot never handles a user's password."
(FR-LNK-08 is satisfied by these rows: device mode is the second adapter.)

## 8. Layout (section 7) and architecture (hexagonal, as the bot's constitution requires)

- `domain/`: `Link` gains `kind`, `subject`, `status`; `DeviceLogin`; pure rules for the polling
  schedule (`next_poll_delay`, `slow_down`) and for "token still valid for 60 s".
- `application/linking.py`: `StartDeviceLogin`, `PollDeviceLogin`, `AccessTokenFor(telegram_user)`,
  `Logout`. New ports in `application/ports.py`: `IdentityProvider` (`start_device()`, `poll(device)`,
  `refresh(token)`, `revoke(token)`, all returning plain dataclasses) and `DeviceLoginStore`.
- `adapters/outbound/oidc.py`: the `IdentityProvider` over httpx with discovery caching.
- `adapters/outbound/postgres_store.py`: the new columns and `device_logins`
  ([`../data-model.md`](../data-model.md) section 11 and its SQL; migration `002_oidc_links.sql`).
- `adapters/inbound/telegram`: `/login`, `/logout` handlers and the poll task.
- `cli`: `links` listing shows kind and status.
- `.env.example`: the variables above, with comments in generic terms.
- Docs: `docs/OPERATOR_GUIDE.md` gains a short "Device sign-in" section (generic: register a public
  client with the device grant enabled at your issuer, set the four variables).
- Tests: fakes for `IdentityProvider` in `tests/application/fakes.py`; unit tests for each row above
  carry `# Covers:` lines. Mark `planned` rows `done` only with those tests.
