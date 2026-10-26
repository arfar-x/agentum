# Telegram bot in the stack

The Telegram bot itself is the `telegram-bot-gateway` git submodule
([telegram-bot-openai-gateway](https://github.com/arfar-x/telegram-bot-openai-gateway)), a client of any
OpenAI-compatible endpoint that knows nothing about this stack. This directory holds only what ties it to
the stack: its compose service, make targets, variables and scripts. After cloning, fetch it with
`git submodule update --init telegram-bot-gateway`. Bumping it is a one-line commit of the new submodule
pointer.

In the stack the bot is opt-in, its state lives in its own database and role on the gateway's Postgres, it
talks to the agent gateway as its endpoint, and its users are linked to keys issued by that gateway.

## Wiring

| Piece | Where |
|---|---|
| Service | [`compose.yaml`](compose.yaml), included by the root `docker-compose.yml` (profile `telegram`); builds the submodule |
| Variables | [`.env.example`](.env.example) copied to `integrations/telegram-bot/.env` (ignored by git) |
| Make targets | [`module.mk`](module.mk), included by the root `Makefile` |
| Scripts | [`scripts/`](scripts): `prepare.sh` (env file, secrets), `setup-db.sh` (role and database), `pair.sh` (linking) |

The service joins only the network it shares with the gateway and the gateway's data network, never
`backend`, and publishes no port. `make backup` copies its env file next to the stack's.

## Commands

```bash
make telegram-setup    # env file, generated secrets, database role and database (idempotent)
# set BOT_TOKEN and DEFAULT_MODEL in integrations/telegram-bot/.env
make telegram-up       # starts the gateway and the bot
make telegram-logs
make telegram-codes    # open pairing codes
make telegram-pair CODE=ABCD2345   # issues a gateway key for the username in the code and links it
make telegram-links
make telegram-unlink TG_ID=123     # or USER_NAME=alice@example.org
make telegram-down
make telegram-test     # unit tests and the PostgreSQL tests of the submodule
```

`telegram-pair` shows the code, issues a gateway API key for the code's username with the label `telegram`,
and pipes its first line into the bot's approval, so the key is never printed or written to a file. It fails
before issuing a key when the code is unknown. If the code expires between the check and the approval, the
issued key stays active; revoke it with `make gateway-key-revoke KEY_ID=...`. `telegram-unlink` removes the
link and the user's sessions but not their gateway key.

## Linking modes

`LINK_MODE` in `integrations/telegram-bot/.env` picks how a Telegram account is tied to a person:

- `pairing` (default): the bot shows a code, an operator approves it with `make telegram-pair` and the bot
  keeps a gateway-issued key. Needs `GATEWAY_ISSUE_KEYS=true` on the gateway.
- `device` (recommended): the person sends `/login`, approves a code at the identity provider, and the bot
  stores only their refresh token. The gateway then sees who they are (their groups and roles) on every
  message, and disabling the account in the identity provider cuts the bot off. Set `OIDC_ISSUER` to the
  realm address (`https://<keycloak-host>/realms/agentflow`); `OIDC_CLIENT_ID` is `telegram-bot`, a public
  client the realm provides (`make idp-realm-apply`; see [`docs/KEYCLOAK.md`](../../docs/KEYCLOAK.md)).
  There is no `telegram-pair` step. Available after the bot's device sign-in and the gateway's token
  support are merged (specs/003-identity-access).

## Database role

`setup-db.sh` creates the role `telegram_bot` and the database `telegram_bot` on the gateway's Postgres,
re-applies the configured password on every run, revokes connection rights from everyone on every database
that accepts connections, and grants them back on the bot's own database only.
