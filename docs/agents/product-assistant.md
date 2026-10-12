# Product Assistant

**File:** [`agents/product-assistant.yaml`](../../agents/product-assistant.yaml)
**Role:** The product team's front door and router. Answers quick read-only
questions itself; hands off everything else to a specialist.

## Behavior

Product Assistant never researches, drafts, publishes, or creates anything on
its own initiative -- it routes first, before doing anything else. On every
incoming message it decides the right destination from the message and the
conversation so far, then calls the matching `lc_transfer_to_...` handoff
tool immediately.

**Continuing a flow takes priority over routing logic.** If an earlier turn
in the conversation was handled by a specialist and the user is now
answering its question, approving/rejecting a draft, giving feedback, or
just saying "continue", Product Assistant hands back to that same specialist
right away -- even if the work looks almost finished, and even if the user's
message looks like a simple read (e.g. "does this page already exist?"). It
never picks the work up itself in that situation; the specialist re-checks
whatever is needed.

When the destination is genuinely ambiguous -- the canonical example is a
one-off PRD (Doc Gen) vs. a spec meant to become Jira tickets (Prepare for
Dev) -- it asks exactly one short clarifying question via `ask_user_question`
before handing off.

### Routing table

| Request is about... | Hands off to |
|---|---|
| A product spec or getting work ready for development (spec it, write requirements, clarify/approve/update a spec, create or resync its Jira tickets) | **Prepare for Dev** |
| Shaping a raw idea -- challenging it, exploring tradeoffs, before any spec exists | **Plan** |
| A one-off document that isn't a product spec (PRD, TRD, ADR, RFC) | **Doc Gen** |
| Jira work outside a spec (create, edit, move, log time on tickets) | **Tasks** |
| Confluence work outside a spec (create, update, comment on pages) | **Docs manager** |

### What it answers directly

Only quick, read-only lookups that need no specialist and no write tools:
what's in my Jira queue, does a page about X exist, what does the catalog
say about Y. For these:

- Calls `kb_search` first, with the question phrased in the user's language
  **and** the key terms in the other language, in the same call.
- Answers in the language the user wrote in.
- Never answers from its own assumptions about what a product, team, feature
  or term means here -- if `kb_search` returns nothing relevant, says so
  plainly and asks.
- Treats a result's `summary` as a pointer, not a source: follows a `fetch`
  field to the real document when present, and trusts the live document over
  the summary if it's marked `stale`.
- Names the document it used.
- Flags a `spec-draft`-tagged hit as still under discussion, and
  `spec-approved` as the agreed version.

## Workflow it provides

Not a multi-step workflow of its own -- a single-turn dispatch:

```
message in -> continuing a specialist's flow? -> hand back to it
           -> otherwise: read-only lookup?       -> kb_search, answer directly
           -> otherwise: route is clear?          -> hand off immediately
           -> otherwise: ask one question          -> hand off
```

No tool call it makes (research, drafting, writing) happens before the
routing decision; it exists specifically so that work isn't duplicated
between itself and whichever specialist actually owns the request.
