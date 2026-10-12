# Agents

The product team's LibreChat agents, defined declaratively in
[`agents/*.yaml`](../../agents/) and applied with `make agent-import` (see
[`docs/AGENT_SYNC.md`](../AGENT_SYNC.md) for the file format and sync
mechanics). This directory documents what each agent actually does and the
workflow it provides -- not how to edit or import them.

## Agents

| Agent | What it is |
|---|---|
| [Product Assistant](product-assistant.md) | The front door -- a pure router to the other five, answering only quick read-only lookups itself. |
| [Plan](plan.md) | Conversational discovery partner that pressure-tests a raw idea before anything is written or built. |
| [Doc Gen](doc-gen.md) | Generates a single one-off document (PRD/TRD/ADR/RFC/product-spec draft) from a brief; never publishes, never persists state. |
| [Prepare for Dev](prepare-for-dev.md) | Owns the full product-spec lifecycle: draft -> save to Confluence -> clarify -> readiness -> approve -> project to Jira. |
| [Tasks](tasks.md) | Reads and creates Jira work items per this team's parent/sub-task conventions. |
| [Docs manager](docs-manager.md) | Reads and publishes to Confluence outside of a spec flow. |

## Shared conventions across all agents

- **Catalog-grounded, not assumption-grounded.** Every agent calls `kb_search`
  before writing about a product, team, system, or term not already given in
  the conversation -- in the user's language and the other one -- and follows
  a hit's `fetch` field to the real document rather than trusting its
  summary. A `spec-draft`-tagged hit is flagged as still under discussion.
- **Approval gates are real, not rhetorical.** Every write-capable agent
  shows the exact draft/target before acting and passes `confirm: true` only
  after LibreChat's own approval prompt has fired; a `requires_confirmation:
  true` result means nothing was written yet.
- **Full tool names only.** Tools are always called by their full
  `..._mcp_agent-skills` / `..._mcp_kb` suffixed names, never the short form.
- **No code, ever.** Current-behavior grounding comes from specs/docs/Jira/
  Confluence via the knowledge base, never from reading the repository.

## Handoff map

```
Product Assistant (router)
 ├─> Plan               (shape a raw idea)
 │     ├─> Doc Gen       (one-off document from the shaped idea)
 │     └─> Prepare for Dev (shaped idea -> spec + tickets)
 ├─> Doc Gen            (one-off PRD/TRD/ADR/RFC)
 │     └─(suggests)-> Prepare for Dev (if the doc is really a spec)
 ├─> Prepare for Dev    (spec lifecycle: draft -> clarify -> ready -> approve -> Jira)
 ├─> Tasks              (Jira work outside a spec)
 └─> Docs manager       (Confluence work outside a spec)
```
