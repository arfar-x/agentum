# Doc Gen

**File:** [`agents/doc-gen.yaml`](../../agents/doc-gen.yaml)
**Role:** Generates a single one-off document (PRD, TRD, ADR, RFC, or
whatever `doc_gen`'s template set currently offers) from a brief or request.
Never publishes it and never creates tickets.

## Behavior

1. Calls `list_skills`/`get_skill` for `doc_gen` to pull the right template's
   live instructions for the requested document type -- it does not assume
   it already knows the template's shape.
2. If the brief references existing product context, pulls real content in
   via `confluence_search`/`confluence_get_page` rather than inventing what
   an existing page says.
3. Follows the returned template instructions to write the document, with
   one deliberate, permanent override: Doc Gen runs in agent/chat mode, not
   filesystem mode. It **ignores** any template instruction about saving to
   disk (`docs/PRDs/...` paths, `YYYY-MM-DD-NN` naming, maintaining
   `CURRENT_STATE.md`) -- it has no filesystem, and it never claims to have
   saved anything.
4. Produces the document as chat markdown, or as a LibreChat Artifact when
   the document is long enough that a side-panel view helps (judgment call,
   or asks if unsure).
5. After presenting the document, always asks what to do with it next
   (publish to Confluence, hand to Prepare for Dev to turn into tickets, keep
   as a draft, revise) -- it never assumes.

**Routing a spec request away from itself:** a request to "spec" something,
write requirements, or prepare a feature for development means `doc_type`
`product-spec` -- a product-level spec in GitHub Spec Kit `spec.md` shape --
unless the user names a different document type explicitly. For that
specific document type, Doc Gen still only *drafts*: it grounds the Current
Behavior section in documents actually read via `kb_search` then the pages
it points to (never code), puts unanswered items under "Questions for
Engineering" rather than guessing, and suggests **Prepare for Dev** for a
spec headed to Jira tickets, since that agent runs the whole clarify ->
approve -> Jira flow.

Doc Gen does not publish documents itself and does not create tickets --- if
asked to do either, it says so and points to the right agent (Docs manager /
Tasks / Prepare for Dev).

**Grounding rule (applies to every document type):** calls `kb_search` before
writing about any product, team, system, or term not already established in
the conversation, in the user's language and the other one; follows a hit's
`fetch` field to the real document rather than its summary; names what it
used. If the catalog has nothing, it says so in the draft instead of
inventing context.

## Workflow it provides

```
brief in -> get_skill(doc_gen) for the live template
         -> kb_search / confluence lookups for real context
         -> write the document per the template
            (strip any "save to disk" instruction -- chat/Artifact only)
         -> present to user
         -> ask: publish? hand to Prepare for Dev? keep as draft? revise?
```

One document, one turn (plus revisions) -- no persisted state across
conversations. Compare with **Prepare for Dev**, which is a multi-stage,
Confluence-persisted pipeline specifically for product specs destined for
Jira.
