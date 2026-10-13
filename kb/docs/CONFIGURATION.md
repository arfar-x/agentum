# Knowledge base configuration

Everything that configures the catalog, and where it lives. The module reads
exactly two things: its env file, `kb/.env.kb`, and its source configuration,
`kb/config/sources.yaml`. It does not read the stack's `.env`.

| File | Tracked template | Holds |
|---|---|---|
| `kb/.env.kb` (gitignored, `chmod 600`) | `kb/.env.example` | Database passwords, source credentials, the summarizer endpoint, timezone, languages, webhook secret |
| `kb/config/sources.yaml` (gitignored) | `kb/config/sources.yaml.example` | *Which* spaces, projects and repositories are catalogued, and on what cadence. Credentials are only ever *named* here (`token_env:`), never written |

`make up` creates both from their templates (`kb/scripts/prepare.sh`, also run by
`make kb-setup`). It is idempotent and never overwrites a file that exists. On
the first creation of `kb/.env.kb` it carries over any `KB_*` values (and the
older `CONFLUENCE_*`/`JIRA_*` spellings of the source credentials) from the
stack's `.env`, so an existing deployment keeps the database password its volume
was initialised with. If there was nothing to carry over it generates the two
passwords. Delete the old `KB_*` lines from `.env` once you have checked the new
file -- they are no longer read, and `make up` says so while they remain.

## Which service sees what

The services are in [`kb/compose.yaml`](../compose.yaml), which the root
`docker-compose.yml` includes with `kb/.env.kb` as its only interpolation source.

| Service | Gets | Why |
|---|---|---|
| `mcp-kb` (query path) | `KB_DATABASE_URL` (as `kb_reader`), `KB_MCP_HOST`, `KB_MCP_PORT` -- nothing else, no env file | It must not be able to see the owner credential or any source credential (NFR-CFG-03) |
| `kb-scheduler`, `kb-cli`, `kb-webhook` | All of `kb/.env.kb`, plus the owner DSN | They sync, so they hold the source credentials and the writable role |
| `kb-admin` (opt-in) | The `KB_ADMIN_*` values, the summarizer endpoint and `KB_TIMEZONE`/`KB_SUMMARY_LANGUAGES`, and a DSN as `kb_admin` -- an explicit list, no env file | A web-facing process must never see a source credential (FR-ADM-15) |
| `kb-db` | The three `KB_POSTGRES_*` values, as Postgres's own `POSTGRES_*` | |

## Variables

All live in `kb/.env.kb` unless a row says otherwise.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `KB_POSTGRES_DB` | no | `agentflow_kb` | Database inside the `kb-db` service. Separate instance from `vectordb` by design -- restoring LibreChat's RAG store can't touch the catalog, and `kb` never holds a credential to LibreChat's data. |
| `KB_POSTGRES_USER` | no | `kb` | Owner role. Sync (and `make kb-init`) connect as this. |
| `KB_POSTGRES_PASSWORD` | yes | generated | **Only takes effect when the database volume is first created.** Changing it later does not change the stored password; it just stops sync connecting. **Every `docker compose` command fails until this is set**, since `kb-db` declares it required. |
| `KB_READER_PASSWORD` | yes | generated | Password for `kb_reader`, the least-privilege role the query path uses: read the catalog, append to the gap log, nothing else. `make kb-init` (re)applies it, so it can be rotated. |
| `KB_CONFLUENCE_*` / `KB_JIRA_*` | for sync | none | A **read-only service account**: sync must see a space to catalog it. Not the per-user credential an agent reads a page with. A base URL plus a PAT, or a username/password pair. |
| `GITLAB_BASE_URL`, `GITLAB_TOKEN` | for GitLab sources | none | A read-only token (`read_api` is enough). The names are what `token_env:` in `sources.yaml` points at. Discovery also accepts `KB_GITLAB_BASE_URL`/`KB_GITLAB_TOKEN`. |
| `KB_SUMMARIZER_URL` | no | none | An OpenAI-compatible **base** URL, ending in `/v1`. A pasted `/v1/chat/completions` is trimmed; a non-URL fails at startup. Unset means sync catalogs documents under their real titles, undescribed. It is a literal value: this module does not read the stack's `VLLM_BASE_URL`. |
| `KB_SUMMARIZER_MODEL` | no | none | The id the endpoint serves. `make kb-check` reports whether it is among them. |
| `KB_SUMMARIZER_API_KEY` | no | none | Optional: sent as `Authorization: Bearer …` when set, omitted when empty -- many self-hosted endpoints need no key. |
| `KB_SUMMARIZER_HOSTNAME`, `KB_SUMMARIZER_HOST_IP` | no | placeholders | Pins the endpoint's host to an IP inside the kb containers, for a host that doesn't resolve from the box. The placeholders are harmless. (The stack's own `MODEL_HOSTNAME`/`MODEL_HOST_IP` are not read here.) |
| `KB_SUMMARY_LANGUAGES` | no | `en` | Comma-separated, e.g. `en,fa`. Every entry is catalogued in each, which is what lets a question in one language find a document written in another. |
| `KB_TIMEZONE` | no | `UTC` | IANA name (e.g. `Asia/Tehran`). The nightly full scrape's `at: "03:00"` means 03:00 *here*; everything else compares instants and is unaffected. An unknown name fails at startup. |
| `KB_SOURCES_APPROVED` | no | `true` | The kill switch: `false` stops every source syncing without editing `sources.yaml`. Overrides the file's own `approved:`, and every command reports which one it used. |
| `KB_WEBHOOK_SECRET` | for the webhook | none | Without it the receiver refuses to start. Set the same value in GitLab's webhook settings. |
| `KB_WEBHOOK_PORT` | no | `8323` | Host port, bound to `127.0.0.1` only. |

### Admin console (optional)

Off unless started with `make kb-admin-up` (compose profile `admin`). See
[`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md#the-admin-console). `make kb-admin-up`
generates the first two when they are empty.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `KB_ADMIN_TOKEN` | to run it | generated | The one credential for the console, at least 32 characters; it refuses to start without it. Sessions are signed with a key derived from it, so changing it signs everyone out. |
| `KB_ADMIN_DB_PASSWORD` | to run it | generated | Password for `kb_admin`, the database role the console uses: it reads everything and writes only its own tables, never the catalog. `make kb-init` creates the role when this is set. |
| `KB_ADMIN_PORT` / `KB_ADMIN_BIND` | no | `8324` / `127.0.0.1` | Where it is published. Widen the bind only behind a TLS proxy you control. |
| `KB_ADMIN_COOKIE_SECURE` | no | `false` | Set `true` behind TLS so the session cookie is never sent in the clear. |
| `KB_ADMIN_SESSION_HOURS` | no | `12` | How long a login lasts. |
| `KB_ADMIN_FORWARDED_ALLOW_IPS` | no | empty | The address of a reverse proxy in front of the console (comma separated; `*` trusts everyone, do not). Only from these is `X-Forwarded-For` believed. Without it, behind a proxy, every visitor is the proxy: five wrong guesses from anyone lock the login for everyone. Also have the proxy pass `Host` unchanged -- the console compares it with the browser's `Origin` -- and set `KB_ADMIN_COOKIE_SECURE=true`. |
| `KB_ADMIN_USER` | no | `0:0` | The user the container runs as. It rewrites the bind-mounted `kb/config/sources.yaml` in place, which a host-owned file only allows as root; set the file's owner here (e.g. `1000:1000`) to avoid that. |

`KB_ADMIN_DB_PASSWORD` is put into a connection URL by `kb/compose.yaml`, so use
letters and digits (what `make kb-admin-up` generates); a hand-picked one with
`@ / : ? #` breaks the URL.

The console reads `KB_SUMMARIZER_*` and `KB_SUMMARY_LANGUAGES` too (for "generate
summary" and the languages of a record). It is given no source credentials.

Set by compose or the image, not by you:

| Variable | Notes |
|---|---|
| `KB_DATABASE_URL` | Built per service: `mcp-kb` connects as `kb_reader`, everything else as the owner. Set it yourself only to run the CLI (`python -m kb status`) against the catalog from elsewhere. |
| `KB_MCP_HOST` / `KB_MCP_PORT` | `0.0.0.0` / `8322` in the container so `api` can reach it; it publishes no port. Default `127.0.0.1` outside. |
| `KB_WEBHOOK_HOST` | `0.0.0.0` in the container; the host side is bound to loopback. |
| `KB_SOURCES_FILE` | `/opt/kb/config/sources.yaml`: where compose mounts `kb/config/sources.yaml`. Only sync and discovery load it; the MCP server never does, so a malformed source config cannot break search. |
| `KB_MIGRATIONS_DIR` | Set in the image, where the package is installed away from the `.sql` files. |

Two query-path tunables exist in code but are deliberately not operator-facing:
`KB_SEARCH_DEFAULT_LIMIT` (hits returned when a caller doesn't say, `8`) and
`KB_STALE_AFTER_HOURS` (how long an entry may go unverified before results mark
it `stale`, `24`).

## Sources

Sources live in `kb/config/sources.yaml`. `make kb-sources-discover` drafts it
from what Confluence, Jira and GitLab actually contain, writing every candidate
disabled. A source syncs once you set `enabled: true` on it; `approved: false`
(or `KB_SOURCES_APPROVED=false`) stops all of them at once. `${VAR}` and
`${VAR:-default}` work anywhere except in comments, resolved from the same
environment the sync services have -- that is, `kb/.env.kb`.

Losing `kb_data` costs the gap log and the sync checkpoints; the catalog itself
is rebuilt from its sources. See [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md)
"Volumes". The database passwords in `kb/.env.kb` are part of that backup:
`make backup` copies the file beside the volumes, and a restored catalog only
accepts the passwords it was made with.
