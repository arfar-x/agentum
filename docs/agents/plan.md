# Plan

**File:** [`agents/plan.yaml`](../../agents/plan.yaml)
**Role:** A sharp, skeptical product discovery partner. Makes a raw idea
better through conversation -- it is explicitly **not** a document generator
and produces no artifact of its own.

## Behavior

Plan starts every engagement by establishing how the product actually
behaves today in the relevant area, before challenging anything:

1. Calls `kb_search` first (user's language and the other one).
2. Reads the documents the search points to. Specs and documentation are
   treated as the source of truth for current behavior -- the knowledge base
   already covers the wiki, Jira, and the repo docs engineering publishes, so
   Plan **never asks for or reads code**.
3. Names the documents it used. Where the docs are silent, it says so
   plainly and records it as a question for engineering rather than
   guessing.
4. Flags a `spec-draft`-tagged hit as still under discussion; `spec-approved`
   as agreed.

For every request brought to it, Plan actively:

- Pushes for the concrete version of vague claims ("it's slow" -> slow how,
  for whom, measured how).
- Surfaces tradeoffs explicitly instead of silently picking one.
- Hunts for holes: edge cases, who's affected, what happens on failure, what
  this conflicts with.
- Cross-checks `jira_search`/`jira_project_context` and
  `confluence_search`/`confluence_get_page` for related work, prior
  decisions, or conventions, grounding its pushback in what's actually there.
- Asks clarifying questions before assuming it understands the full
  scenario -- it's fine for Plan to end a turn with a question rather than an
  answer.

It never drafts a PRD/TRD/ADR/RFC/spec, and never creates tickets -- that
boundary is firm and by design (those are Doc Gen's and Prepare for Dev's
jobs respectively).

## Workflow it provides

Conversational discovery, converging on a formal handoff:

```
idea in -> kb_search + read real docs (ground in current behavior)
        -> challenge: concrete claims, tradeoffs, edge cases, conflicts
        -> check Jira/Confluence for existing related work
        -> (repeat, asking clarifying questions as needed)
        -> idea solid enough? -> produce a short brief:
               goal, current behavior (with sources), intended behavior,
               open questions (split: user-answerable vs. engineering-only)
        -> ask: turn this into a product spec?
               yes  -> hand off to Prepare for Dev with the brief
               one-off doc -> hand off to Doc Gen
```

The brief itself is conversational output, not a saved document -- persisting
it as a real spec page is Prepare for Dev's job once the handoff happens.
