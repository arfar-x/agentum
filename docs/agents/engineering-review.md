# Engineering Review

**File:** [`agents/engineering-review.yaml`](../../agents/engineering-review.yaml)
**Role:** For engineers. Reviews a product spec, questions it, and keeps the
spec's **engineering page** -- the record of technical decisions, the
contracts agreed with other teams, and the open technical questions. It is a
record of decisions, not an implementation plan (the team's own Spec Kit plan
does that). It never edits the spec itself: when engineering wants the spec to
change, it writes a change proposal and product decides.

## The engineering page

One page per spec, a **child of the spec page**, titled `<spec title> -
Engineering`. Like a spec page it keeps its Markdown in a collapsed source
block (`engineering.md`, which developers copy next to `spec.md`), is created
once and then changed with one `confluence_patch_page` per round, and carries
no labels. The state lives in the page; comments only tell people something
needs their attention (Confluence comments here are page-level and
unthreaded). Every item has an id that never changes; a replaced item is
marked "Superseded by ...", never deleted.

| Section | Items | Notes |
|---|---|---|
| Header | spec link, spec revision reviewed, parties | each party is a person or a role; external teams included |
| Decisions | `ED-001`... | decision, context, options, rationale, decided by, date; `Proposed` while discussed, `Decided` once the engineer has chosen, `Superseded by ED-n` |
| Contracts | `CT-001`... | what each party provides and expects (interface, data, events, design handoff, limits); status `Discussing` / `Agreed by <parties>, <date>` / `Changed <date>: <why>` with a short history inside the item |
| Technical Questions | `TQ-001`... | addressed to a person, role or team; `Open` / `Answered`, with where the answer landed |
| Spec Change Proposals | `CP-001`... | spec id touched, current text, proposed text, why, cost of not changing; status and decision are set by product |
| Technical Dependencies | | each linking to its contract |
| Log | | dated one-liners |

Contracts change often, so they live only here, with no labels and no copies.
The spec stays product-level: product dependencies are written out in it,
technical ones are a link to this page.

## Behavior

Engineering Review talks to the engineer directly (`ask_user_question`, one
question per call, each with a recommended answer) and makes its Confluence
writes itself, each behind LibreChat's approval prompt, always with
`confirm: true`. It announces its stage each turn and answers in the language
of the user's message.

**0. Load.** Finds the spec, reads the live page and its comments, then looks
at the spec's children for an existing engineering page and resumes from it.
It never creates a second one.

**1. Understand.** A short stage: one `kb_search`, one `jira_search`, at most
five documents read. GitLab (read-only) is a helper for **at most five
calls**, only to check a specific claim the documents leave open; whatever it
could not confirm becomes a Technical Question instead of more reading. If
GitLab isn't set up for the user it carries on without it.

**2. Review.** Shows its findings right away, each sorted into one kind:
Decision, Contract, Technical Question, Spec Change Proposal, or
engineering-only (told to the engineer and kept off the page). Then discusses
them one step at a time. The engineer decides what is recorded and proposed;
proposals describe the behavior that should differ, not technology.

**3. Record.** One create (first time) or one patch (afterwards) of the
engineering page per round, reported by id.

**4. Notify.** Offers, and posts only if the engineer agrees, short comments
naming an id and the ask: on the spec page for new change proposals
(addressed to the spec's owner), on the engineering page for contracts and
questions that need a named person or team.

**5. Resume.** Reads the page and comments, folds replies into questions,
contracts and decisions (with the engineer's go-ahead), reports what product
decided on each proposal, and marks a contract `Agreed` with names and date
once the parties agree.

### Rules

- Never edits the spec page body, status or labels.
- Specs and docs come first; names the documents used; a `spec-draft` hit is
  under discussion, `spec-approved` is agreed.
- Engineering review is **not** an approval gate: a spec can be approved
  while proposals are open.
- A proposal against an approved spec is still only a proposal; an accepted
  one gives the spec a new revision.
- `ask_user_question` always goes alone, never in the same step as a write,
  and `confluence_patch_page` takes list-valued arguments.

## Workflow it provides

```
spec found -> read page + comments + children (engineering page?)
  -> understand: kb, jira, <=5 docs, <=5 GitLab calls
  -> findings by kind (ED / CT / TQ / CP / engineering-only)
  -> discuss with the engineer, one question at a time
  -> create or patch the engineering page
  -> offer notification comments
  ...later: resume, fold in replies, report product's decisions on CPs

product side (Prepare for Dev, stage 2b):
  CP Proposed -> accept / counter / reject -> patch spec (if accepted)
              -> patch only the CP's status + decision on the engineering page
```
