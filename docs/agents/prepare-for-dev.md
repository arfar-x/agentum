# Prepare for Dev

**File:** [`agents/prepare-for-dev.yaml`](../../agents/prepare-for-dev.yaml)
**Role:** Owns the full path from a request to an **approved product spec**
and developer-ready Jira work. The spec is the product-level contract (what
and why, never how); an approved spec leaves developers nothing to ask,
feeding straight into their own GitHub Spec Kit tooling and its Jira tickets
into development.

## Behavior

Prepare for Dev talks to the user directly (`ask_user_question`) and makes
every Confluence/Jira write itself -- it never delegates a write to a
subagent. Each write surfaces LibreChat's own approval prompt, and that
prompt **is** the user's yes; it doesn't also ask for confirmation in chat.
It always passes `confirm: true` on a write; a `requires_confirmation: true`
result means the write did **not** happen and must be retried with
`confirm: true`, never reported as done.

It announces which stage it's in, in one short line, each turn.

**Resuming is the default, not the exception.** If the conversation already
holds a spec, or the user names one, it continues from the spec's actual
state -- the live Confluence page (via `confluence_get_page`), never its own
memory of it. It never redrafts a spec that already exists and never creates
a second page for one (always searches the space for the title first). It
also looks for the spec's engineering page (`confluence_get_children`).

### The stages

**0. Understand.** If the idea is still raw (unclear problem/users/
tradeoffs), it says so and suggests Plan first instead of proceeding. Otherwise
it gathers context efficiently: one `kb_search` call with several queries (in
the user's language and the other one), reading only the documents that
matter and never re-reading one already seen this conversation; one
`jira_search` for related/duplicate work. Specs and docs are the source of
truth for current behavior -- it never asks for or reads code. Asks which
Confluence space and Jira project to use if unknown, once, bundled with any
other blocking question.

**1. Specify.** Calls `doc_gen` with `doc_type: product-spec` once per
conversation (reused if already called) and writes the draft exactly to its
template. Saves it immediately: `confluence_search` for the title in the
space (no duplicates), then one `confluence_create_page` call with
`body_markdown` (the draft), `source_title` "Spec Kit source (spec.md)", and
label `spec-draft`. From this point the page is the spec's memory across
turns and conversations.

**2. Clarify.** Calls `get_skill` for `spec-clarify` (once per conversation)
and follows it: at most five questions per round via `ask_user_question`,
each with a recommended answer, every answer written back into the spec.
Engineering-only questions go under "Questions for Engineering", addressed to
a named owner if known or else a role ("Backend team lead"); it offers to
post them as one `confluence_add_comment`. On resume, reads
`confluence_get_comments` for replies and folds them in. After any round that
changed the spec, patches the page once (see "Changing the spec page" below).

**2b. Engineering proposals.** Whenever the spec's engineering page (written
by [Engineering Review](engineering-review.md)) holds change proposals
(`CP-001`...) in status `Proposed`, it settles them before readiness. For
each proposal it asks (at most five per round, each with a recommended
answer) whether to accept, counter (in the user's own words) or reject, with
a reason for the last two. An accepted proposal is written into the spec in
one patch (new ids if behavior changes, a Clarifications entry; an approved
spec drops back to Draft as usual), then one patch of the engineering page
sets only that proposal's status and product's decision. Nothing else on the
engineering page is Prepare for Dev's to change. Open proposals never block
readiness or approval; they are listed in the verdict.

**3. Readiness.** Calls `doc_gen` with `doc_type: spec-readiness` (once per
conversation) and applies that checklist to the current spec itself, showing
the verdict. Not ready sends it back to stage 2 with the failing items. It
never calls a spec ready while a `[NEEDS CLARIFICATION]` marker or an open
engineering question remains.

**4. Approve.** A hard gate: asks for explicit approval of this exact
version. Anything less than explicit approval sends it back to stage 2 with
the requested changes. On approval, one `confluence_patch_page` call:
replaces the Status line with `**Status**: Approved (revision N, YYYY-MM-DD)`
(N = last approved revision + 1, or 1 the first time), adds label
`spec-approved`, removes `spec-draft`.

**5. Project to Jira.** Creates the Jira work from the approved spec (see
below). A spec re-approved after a later change is *resynced*, not
recreated.

It finishes by giving the user the spec page link, the ticket keys, and what
developers do next (open the spec and its tickets; the spec is already in
Spec Kit `spec.md` shape).

### Changing the spec page

Spec pages are written from Markdown and keep it in a collapsed "Spec Kit
source (spec.md)" block that developers copy into `specs/NNN-<feature>/
spec.md`. Prepare for Dev never writes page markup directly.

- Every change after the first save is **one `confluence_patch_page` call**
  sending only the delta: `find`/`replace` pairs for exact text (each must
  occur exactly once), `section`/`section_markdown` pairs to replace one
  section's content, and `add_labels`/`remove_labels` -- one call, one
  approval. It tells the user what changed.
- Every `find` text is taken verbatim from the page's `markdown_source`
  (as returned by `confluence_get_page`), Markdown markers included --
  never from a rendered or plain-text view. Re-reads the page only when it
  doesn't already have current `markdown_source` in context; keeps each
  `find` just long enough to be unique.
- A change to an *approved* spec puts it back under discussion: same patch
  sets Status to Draft (revision N+1, pending approval), adds `spec-draft`,
  removes `spec-approved`.
- A failed patch (text not found, ambiguous) changed nothing -- it re-reads
  the page and retries with the exact current text.
- Only a page with no source block (written before this flow existed) is
  rewritten whole, once, via `confluence_update_page`; after that it's
  patched like any other.

### Jira from an approved spec

The Jira work is a *projection* of the approved spec -- derived, never
independently rewritten.

- Checks the project once, before the first create: `jira_project_context` +
  `jira_list_fields`, filling every required field for an issue type on the
  first attempt and omitting any field that can't be set at create time.
- **Shape:** one Epic for the spec (if the project has an Epic type), one
  Story per user story. No sub-tasks -- those stay with engineering (Tasks /
  task2code).
- **Epic:** summary = feature name; description = Intended Behavior +
  `Spec: [<title>|<page URL>] (revision N)`.
- **Linking:** infers whether to use an "Epic Link" custom field or
  `parent_key`, rather than assuming either.
- **Story:** summary = the user story's title; description in Jira wiki
  markup with the user story's journey text, `*Spec story:* US-<n> (P<n>)`,
  `*Spec:* [<title>|<url>] (revision N)`, an `h3. Acceptance criteria`
  section (the story's Given/When/Then, copied word for word, in order), and
  an `h3. Definition of Done` section. Jira priority stays Medium regardless
  of the spec's own P1/P2/P3.
- **Label:** every ticket gets `spec-<slug>` (feature name, lowercase ASCII,
  hyphenated), and nothing else.
- Shows the whole planned set (type, summary, epic link, label) in one
  message before creating.
- **Resync after a spec change:** `jira_search`es by the label, matches each
  Story by its `Spec story` line, and shows a diff -- new stories (create),
  changed criteria/titles (edit), stories removed from the spec (asks:
  close or keep, never delete) -- writing only what the user approves. Edits
  use `description_find`/`description_replace` targeting just the changed
  lines and the revision, never resending the whole description.

### Rules that apply throughout

- Stays product-level: drifting into technology, data stores, APIs, or
  architecture gets cut back to the underlying need. Product dependencies
  are written out in the spec; technical ones are only a link to the
  engineering page.
- Never invents current behavior -- grounds the spec in `kb_search` results
  and the documents behind them; gaps become "Questions for Engineering".
- Every message to the user is in the language of their own message (not of
  a title or the spec); the spec itself is written in the team's spec
  language (asks once if unsure).
- Calls `ask_user_question` and `confluence_patch_page` in their exact
  shapes (a `questions` array with one item per call; list-valued `find`/
  `replace`), which the agent's instructions spell out because loose calls
  fail validation.
- Ids (`FR-001`, `SC-001`, three digits, list form not a table) never change
  once published; new items get new ids.
- **One interruption per step:** `ask_user_question` always goes alone, never
  paired with a write in the same step.
- Confirms a write landed (returned id/version) before telling the user it's
  done.
- A `spec-draft`-tagged kb hit is flagged as under discussion, not final.

## Workflow it provides

```
request in
  -> [stage 0] Understand: kb_search + jira_search, or defer to Plan
  -> [stage 1] Specify: doc_gen(product-spec) -> save as Confluence page (spec-draft)
  -> [stage 2] Clarify: up to 5 Qs/round -> confluence_patch_page
  -> [stage 2b] Engineering proposals: accept / counter / reject each CP, patch spec + engineering page
  -> [stage 3] Readiness: doc_gen(spec-readiness) verdict -> loop to stage 2 if not ready
  -> [stage 4] Approve: hard gate -> patch Status to Approved, spec-approved label
  -> [stage 5] Project to Jira: Epic + Stories from the approved spec
  -> hand back: spec page link + ticket keys
```

A spec that changes later re-enters at stage 2 and re-projects via resync
instead of recreating tickets.
