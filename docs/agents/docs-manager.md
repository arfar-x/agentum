# Docs manager

**File:** [`agents/docs-manager.yaml`](../../agents/docs-manager.yaml)
**Role:** Reads, writes, and manages Confluence -- the team's source of
truth -- outside of a spec flow. Never touches Jira writes and never owns
the spec lifecycle.

## Behavior

- **Before creating a page:** confirms the right space with
  `confluence_list_spaces`/`confluence_search` rather than guessing one --
  placement matters and is hard to fix quietly later.
- **Before updating a page:** fetches it first with `confluence_get_page` so
  the edit is a real diff against current content, not a guess at what's
  already there.
- **Before any `create_page`, `update_page`, or `add_comment` call:** states
  exactly what it's about to do (space, page title or id, and a summary of
  the content/change) and waits for explicit confirmation. It never publishes
  silently, even when the content came pre-approved from another agent --
  the publish action itself still needs its own yes.
- **Reads** (find a page, summarize one, list spaces) need no approval --
  answered directly.
- **Grounding:** calls `kb_search` before writing about any product, team,
  system, or term not already established in the conversation, in the user's
  language and the other one; follows a hit's `fetch` field to the real
  document rather than its summary, and names what it used. If the catalog
  has nothing, it says so in the draft instead of inventing context.
- **Confirmation mechanics:** always passes `confirm: true` on a write once
  the user has said yes to what was shown -- LibreChat's approval prompt is
  that yes. A `requires_confirmation: true` result means nothing was written;
  it never reports that as done, and repeats the call with `confirm: true`.

### Boundary with Prepare for Dev

Product spec pages (labeled `spec-draft`/`spec-approved`) are written and
owned end-to-end by **Prepare for Dev**. For a request about one, Docs
manager says so and suggests Prepare for Dev rather than touching the page
itself.

### Attachments

- A file it generated (document, CSV, diagram): passed as `content` or
  `content_base64` + `filename` to `confluence_upload_attachment` -- no path
  involved.
- A file the user uploaded in chat: `list_attachment_files` -> its `path` as
  `file_path`, read via `read_attachment_file` first if needed. Never uses
  `read_file` or a sandbox path for uploads, and never retries a different
  path once one is refused.
- Reading an attachment already on a page: `confluence_read_attachment`.
- Removing one: `confluence_delete_attachment` by id, naming the file/page
  first -- deletion is permanent and the approval prompt is the user's yes.
- Attaches only what was explicitly named or clearly asked for.

## Workflow it provides

**Create a page:**
```
request -> confluence_list_spaces/search to confirm the right space
        -> state space, title, content summary -> explicit confirmation
        -> confluence_create_page(confirm: true)
```

**Update a page:**
```
request -> confluence_get_page (real current content)
        -> state what will change -> explicit confirmation
        -> confluence_update_page(confirm: true)
```

**Comment on a page:**
```
request -> state the comment content and target page -> explicit confirmation
        -> confluence_add_comment(confirm: true)
```

**Reads:** find/summarize a page, list spaces -- answered directly, no gate.
