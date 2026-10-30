# Contract: YAML schemas

Three files gain fields. All are validated by typed models at load time; unknown fields are
rejected; an invalid file stops startup naming file and problem (FR-CFG-01).

## 1. Agent file: `agent-gateway/config/agents/*.yaml` (unit U3)

New optional top-level block `access`.

```yaml
version: 1
id: product-assistant
# ...existing fields...
access:
  groups: [product, engineering]     # who may use the agent ...
  roles: [agent-user]                # ... by group OR by role
  write_groups: [leads]              # who may use its approval-gated write tools ...
  write_roles: [agent-writer]        # ... by group OR by role
```

| Field | Type | Rule |
|---|---|---|
| `access` | object, optional | absent = every signed-in person may use the agent and its write tools |
| `access.groups`, `access.roles` | lists of strings, optional | non-empty; each a valid name; no duplicates. A caller may use the agent if it has **any** listed group **or any** listed role. Neither given = everyone. |
| `access.write_groups`, `access.write_roles` | lists of strings, optional | same rules, for approval-gated write tools. Neither given = anyone who may use the agent. |

- `access: {}` is rejected ("give groups, roles, write_groups, write_roles or remove the block").
- `check` warns when a write name is covered by no use rule, and when a group or role name is in no
  directory row (typo detection; skipped when the directory is empty).
- Semantics: data model, section 9. A name containing a comma is invalid.
- Groups and roles are created and assigned in the identity provider, never in this file.

JSON-Schema fragment for the contract file `agent-file.schema.yaml`:

```yaml
access:
  type: object
  additionalProperties: false
  minProperties: 1
  properties:
    groups:       { type: array, minItems: 1, uniqueItems: true, items: { $ref: '#/$defs/group' } }
    roles:        { type: array, minItems: 1, uniqueItems: true, items: { $ref: '#/$defs/group' } }
    write_groups: { type: array, minItems: 1, uniqueItems: true, items: { $ref: '#/$defs/group' } }
    write_roles:  { type: array, minItems: 1, uniqueItems: true, items: { $ref: '#/$defs/group' } }
$defs:
  group: { type: string, minLength: 1, maxLength: 128, pattern: '^[^,\x00-\x1f]+$' }
```

## 2. Tool servers: `agent-gateway/config/mcp-servers.yaml` (units U4, U7)

```yaml
servers:
  kb:
    url: http://mcp-kb:8322/mcp
    timeout: 30
    forward_identity: true            # U7: send X-KB-Groups and X-KB-Trust
  agent-skills:
    url: http://mcp-agent-skills-gateway:8321/mcp
    timeout: 90
    header_prefix: X-Agent-Skills-Env-
    client_terms: [jira, confluence, atlassian, gitlab, glab]
    credentials:                      # U4: strings (as today) or objects
      - name: JIRA_BASE_URL
        label: Jira address
        secret: false
        required: true
      - name: JIRA_PASSWORD
        label: Jira password or personal access token
        secret: true
        required: true
      - GITLAB_TOKEN                  # plain string = {name, label=name, secret=true, required=false}
    test:                             # U4: optional, read-only
      tool: jira_my_work
      args: {}
```

| Field | Type | Default | Rule |
|---|---|---|---|
| `forward_identity` | bool | `false` | when true, calls to this server carry `X-KB-Groups` and `X-KB-Trust` (needs `KB_TRUST_SECRET`; startup warns if true and the secret is empty) |
| `credentials[]` | string or object | | object keys: `name` (env-style, `^[A-Z][A-Z0-9_]*$`), `label` (text), `secret` (bool, default true), `required` (bool, default false) |
| `test` | object, optional | none | `tool` must name a tool the server exposes; `args` an object; the portal only offers a test button when set |

A variable that is not declared can neither be stored nor forwarded (FR-CRD-05, FR-CRD-08).
`secret: false` values (addresses, default project) may be shown back in the portal; `secret: true`
never.

## 3. Knowledge base sources: `kb/config/sources.yaml` (unit U6)

```yaml
sources:
  - id: engineering-wiki
    kind: confluence
    # ...existing fields...
    groups: [engineering]          # who may see this source's entries; absent or [] = everyone
```

| Field | Type | Rule |
|---|---|---|
| `groups` | list of strings, optional | each a valid group name (no comma, no control character, 1 to 128 chars), no duplicates. Absent or empty = unrestricted. Rejected under `defaults:` (a default restriction would silently apply to sources added later; set it per source). |

Operator guidance (documented in the kb operator guide, unit U6): the restriction applies to what
`kb_search` and `kb_get` return, including stored text (C6). It does not remove a document from the
source system and does not change who can read it there. Removing `groups` publishes the source to
every caller at the next scheduler run.
