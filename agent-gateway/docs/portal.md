# The portal

A small server-rendered web page set under `/portal` on the gateway's own port. A signed-in person
manages their own model key, their tool credentials, and (when enabled) gateway-issued keys. Present
only when `PORTAL_BASE_URL` is set; otherwise every `/portal/*` path is 404. Contract:
`specs/003-identity-access/contracts/http-portal.md`.

## Settings

| Variable | Meaning |
|---|---|
| `PORTAL_BASE_URL` | Public origin, `https://...` (`http://localhost[:port]` is accepted for local use; the cookie's `Secure` flag is dropped only then). No path. |
| `PORTAL_SESSION_SECRET` | At least 32 bytes. Signs the `portal_session` and `portal_login` cookies; changing it ends every session. |
| `GATEWAY_ISSUE_KEYS` | The issued-keys page exists only when this is true. |
| `GATEWAY_MODEL_KEY_LOGIN` | `off`, `read-only` (default) or `full`: shapes the wording of the model-key page about the key acting as a login. |

Sign-in goes through the provider-neutral `PortalLogin` port (authorization code flow with PKCE
S256, state and nonce kept in the signed `portal_login` cookie). The portal itself reads no
provider settings.

## Pages

- **Model key** (`/portal/key`): register, replace or revoke. Write-only; only "ends in ...abcd" is shown.
- **Tool credentials** (`/portal/credentials`): one card per tool server that declares credentials.
  A variable that is not declared in `config/mcp-servers.yaml` cannot be stored, here or with
  `creds set`. Values of `secret: true` variables are never shown back; `secret: false` ones are.
  A server with a `test:` block gets a "Test these credentials" button that calls the declared
  read-only tool with the person's own credentials and shows "works" or a masked error.
- **Gateway keys** (`/portal/issued`): create (label, 1 to 365 days, agents, write), the secret is
  shown once on the response; revoke the person's own keys.
- **Recent activity** (`/portal/activity`): the person's own audit events.

## Declaring credentials

```yaml
servers:
  agent-skills:
    credentials:
      - {name: JIRA_BASE_URL, label: Jira address, secret: false, required: true}
      - {name: JIRA_PASSWORD, label: Jira password or personal access token, secret: true, required: true}
      - GITLAB_TOKEN          # a plain name = secret, not required, labelled with its name
    test:
      tool: jira_my_work      # read-only; called with the person's stored credentials
      args: {}
```

## Safety properties

Every state-changing request is a form `POST` carrying the session's CSRF token and an `Origin`
(or `Referer`) equal to `PORTAL_BASE_URL`, or it is refused with 403 and has no effect. Sessions last
8 hours at most and 30 minutes idle, and each request re-checks that the person is enabled in a
fresh directory row. Responses are `no-store`, framed never, send no referrer to other sites (`same-origin`, because browsers send `Origin: null` with `no-referrer`, which would make the origin check refuse every form), and carry a content security policy
without inline script or style. Every change writes its audit entry in the same transaction as the
change, with the person as actor.
