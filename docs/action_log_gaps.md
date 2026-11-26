# Action-log reconstructability — gap list

Goal: reconstruct research state from the SQLite action log + immutable artifacts
as far as practical. Updated after the audit MVP (actor tagging, mutator
after-state, TUI routed through ops).

## Reconstructable today

- Discovery / metadata / relationships / taxonomy / coverage ops (artifacts;
  cache hits mint events referencing prior artifacts).
- **`query.run` / `query.inspect`** — exploratory and saved query executions with
  immutable record artifacts; `--save` persists exploratory (`in_report=false`).
- **`query.patch`** — composition add/remove, frequency / head|tail scope, rename;
  after-state artifact includes before/after query dict.
- **`remark.add` / `remark.remove`** — query remarks with optional series + span.
- Failed ops (status + error; no artifact overwrite).
- Curation: promote (event or `--query`), copy, annotate, workspace add/rm
  (after-state artifacts).
- **Actor labels** on events: `cli` (CLI default), `tui` (InvestigationHost),
  `agent` / `agent:<name>` via `--actor` / `JST_ACTOR`. Filter with
  `jst actions log --actor-filter …` or Shift+L → `a` in the TUI.
- Research-state snapshot: `jst actions state` (not a mutation journal).
- Feedback outbox rows (separate table).
- `jst actions rerun <event-id>` for idempotent discovery/metadata/query ops.

## Remaining gaps

### 1. Not every TUI keystroke is an op

Chart pan/zoom, nav, and chrome-only interactions are not logged. Composition,
workspace collect/rm, remarks, rename, scope, and promote **are** routed through
`run_operation`. Prefer `jst actions` when an agent needs a clean trail.

### 2. Direct YAML / file edits

Hand edits under `~/.jstdata/projects/<id>/` bypass the log entirely.

### 3. Deep series observations

`jst api series observations` (unbounded history) is not yet a logged action.
Prefer shallow `jst actions query run --tail/--head` for research; document
deep pulls as out-of-band when needed.

### 4. Historical TUI reopen is MVP

Activity drawer (Shift+L) + artifact JSON modal reopen history without API calls.
Full interactive view hydration from artifacts (charts/tables matching live
screens) is still partial — JSON inspect is the guaranteed path.

### 5. Imperfect fold-forward, not undo

The log supports audit (“what happened at T”) better than replay-as-undo. Do not
treat event order as a guaranteed reconstruction of every YAML field if other
writers raced or files were hand-edited.

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
