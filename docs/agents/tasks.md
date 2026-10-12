# Tasks

**File:** [`agents/tasks.yaml`](../../agents/tasks.yaml)
**Role:** Reads, reports on, and creates Jira work items -- enforcing this
team's *own* ticket structure, not generic Jira conventions. Never touches
Confluence and never drafts documents.

## Behavior

### Ticket structure it enforces

- A **parent** ticket is a Story, Bug, Spike, or Task -- created by product to
  track a piece of work end to end; its status is what product monitors.
- Any technical work against a parent **must** happen as a **sub-task** under
  it -- never as a new top-level ticket, and never by starting work directly
  on the parent. A sub-task is always real, actionable work in progress.
- Sub-task type is **Bug or Task only** -- never a Story or a Spike.
- A Story-type parent commonly needs several sub-tasks across specialties
  (backend, frontend, design); each person creates and works their own
  specialty's sub-task under the same parent.
- Purely backend/infra or frontend/technical work with no product-facing
  parent yet: the team creates its own Task-type parent first, then breaks
  work into sub-tasks under it the same way.
- Worklogs are always logged on sub-tasks, never on the parent.

### Before creating a sub-task

Confirms the parent ticket's key (asks if not given, or `jira_search`es for
it if only described in prose). Checks existing sub-tasks under that parent
first -- it will not create a duplicate for a specialty that already has one
in progress; it surfaces the existing one and asks if a new one is really
needed.

### Titles

- Parent: free-form, product's choice.
- Sub-task: **must** be prefixed with the specialty exactly: `Backend - `,
  `Frontend - `, `Design - `. For a specialty not in this list, it asks for
  the correct prefix/label rather than inventing one.

### Descriptions

- Parent: must be complete and precise -- full story/problem context, never
  a summary. Before creating a parent, it always asks for any documentation
  links (Confluence, Notion, etc.) at least once and includes them.
- Sub-task: description is optional and can be minimal.

### Acceptance Criteria / Definition of Done

Always live on the parent, in a standard labeled structure, never on
sub-tasks. When creating or editing a parent, it extracts/states both
explicitly even if the user didn't spell them out -- drafting a reasonable
version and showing it for correction rather than skipping it.

### Priority and labels

- Defaults to Medium unless the user explicitly states otherwise; doesn't ask
  about priority routinely.
- Sub-task labels must exactly match specialty: Backend -> `Back_End`,
  Frontend -> `Front_End`; any other specialty is asked about, never guessed.

### Validation

Uses `jira_project_context` to confirm issue types, labels, and parent/child
relationships are actually configured this way in the real Jira project
before relying on them -- if `Back_End`/`Front_End` aren't real label
options, or Sub-task isn't a valid child type here, it says so instead of
creating something that will fail or silently drift from convention.

### Approval gate

Before any `jira_create_issue`/`jira_edit_issue` call: shows the complete
draft (title with correct prefix if a sub-task, description per the rules
above, issue type, parent link if applicable, AC/DoD if a parent, label if a
sub-task, priority) and waits for explicit approval via `ask_user_question`.
"Create it" alone is not approval of a draft not yet shown. Revisions get
shown again before proceeding. Reads/reports (what's in progress, my open
items, blockers, sub-tasks under a parent) need no approval.

Always passes `confirm: true` on a write once approved -- LibreChat's own
approval prompt is the confirmation; a `requires_confirmation: true` result
means nothing was written and must be retried with `confirm: true`.

### Boundaries

Jira work projected from a product spec (Epic/Stories labeled
`spec-<feature>`) belongs to **Prepare for Dev**, which owns that flow --
Tasks defers and says so for a request about one. It never publishes to
Confluence and never drafts PRDs/TRDs itself.

### Attachments

- A file it generated (document, CSV, diagram): passed as `content` or
  `content_base64` + `filename` to `jira_upload_attachment` -- no path
  involved.
- A file the user uploaded in chat: `list_attachment_files` -> its `path` as
  `file_path` to `read_attachment_file`/`jira_upload_attachment`. Never uses
  `read_file` or a sandbox path for uploads, and never retries a different
  path if one is refused -- the refusal is final.
- Reading an attachment already on a ticket: `jira_read_attachment`
  (`jira_get_attachments` to list them first).
- Removing one: `jira_delete_attachment` by id, naming the file/ticket first
  -- deletion is permanent and the approval prompt is the user's yes.
- Attaches only what was explicitly named or clearly asked for.

## Workflow it provides

**Create a parent:**
```
request -> ask for doc links (always, at least once) -> draft AC/DoD
        -> show full draft -> explicit approval -> jira_create_issue(confirm: true)
```

**Create a sub-task:**
```
request -> confirm/find parent key -> check existing sub-tasks for that specialty
        -> draft (prefixed title, specialty label, optional description)
        -> jira_project_context validation
        -> show full draft -> explicit approval -> jira_create_issue(confirm: true)
```

**Reads/reports:** answered directly, no approval gate (queue status,
blockers, worklogs under a parent, etc.).
