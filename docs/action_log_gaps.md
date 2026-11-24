# Action-log reconstructability — gap list

Goal: reconstruct research state from the SQLite action log + immutable artifacts
as far as practical. Updated after completing Workstream B capability outline.

## Reconstructable today

- Discovery / metadata / relationships / taxonomy / coverage ops (artifacts;
  cache hits mint events referencing prior artifacts).
- **`query.run` / `query.inspect`** — exploratory and saved query executions with
  immutable record artifacts; `--save` persists exploratory (`in_report=false`).
- Failed ops (status + error; no artifact overwrite).
- Curation: promote (event or `--query`), copy, annotate, workspace add/rm.
- Research-state snapshot: `jst actions state` (not a mutation journal).
- Feedback outbox rows (separate table).
- `jst actions rerun <event-id>` for idempotent discovery/metadata/query ops.

## Remaining gaps

### 1. TUI workspace / view-tree mutations are not ops

Staging via Space / `:search`, `:add` / `:rm` view nodes, and drawer edits write
through the batch writer to investigation YAML but are not `run_operation`
events. Agents should mutate via `jst actions workspace|query|annotate` when an
audit trail is required.

### 2. Direct YAML / file edits

Hand edits under `~/.jstdata/projects/<id>/` bypass the log entirely.

### 3. Deep series observations

`jst api series observations` (unbounded history) is not yet a logged action.
Prefer shallow `jst actions query run --tail/--head` for research; document
deep pulls as out-of-band when needed.

### 4. Historical TUI reopen is MVP

Activity drawer (`A` / `:activity`) + artifact JSON modal reopen history without
API calls. Full interactive view hydration from artifacts (charts/tables matching
live screens) is still partial — JSON inspect is the guaranteed path.

### 5. Actor attribution is thin

Events may carry `actor`, but CLI agents often omit it. Filter by project /
investigation, not reliably by “which agent.”

### 6. Feedback is rich but not pixel-perfect

Diagnostics now include workspace snapshot, view params/size, recent events, and
truncated artifact snippets (secrets redacted). Not a full widget-tree replay.

### 7. Soft-deprecated `jst api`

Still available for human debugging; agents should not use it for the audited
path. Overlapping research ops are covered by `jst actions`.

## Intentional non-goals (for now)

- Daemonized live tail of another process’s ops
- Replaying the log into a new project as an undo stack
- Remote feedback delivery / retry (Workstream C)
- Multi-tab remote publish wire format (C)
- Full time-travel branching (parent §9.5 / §18)
