# Action-log reconstructability — gap list

Goal: reconstruct research state from the SQLite action log + immutable artifacts
as far as practical. This documents what **cannot** (yet) be fully rebuilt from
the log alone after Workstream B.

## Reconstructable today

- Logged `jst actions` discovery / metadata / relationships / taxonomy ops
  (inputs + artifact payloads; cache hits still mint events referencing prior
  artifacts).
- Failed ops (status + error string; no artifact overwrite).
- Curation ops: `curation.promote_event`, `curation.promote_query`,
  `curation.copy` (inputs + resulting query summaries in the event payload).
- Feedback outbox rows (separate table; not replayed into investigations).
- Workspace / query **snapshots** via `workspace.show` / `query.list` events
  (point-in-time reads, not a full mutation journal).

## Gaps

### 1. No logged query-run / observations path

Exploratory value pulls still often go through soft-deprecated `jst api query`
or TUI view loads. Those paths do **not** append action-log events or artifacts.
You cannot replay “what numbers did the agent see?” from the log unless the work
went through a logged op (or was promoted and re-fetched later).

### 2. TUI workspace / view-tree mutations are not ops

Staging via Space / `:search`, `:add` / `:rm` view nodes, drawer edits, and
annotation text updates write through the batch writer to investigation YAML but
are not first-class `run_operation` events. Reconstructing the exact view tree
or workspace bag requires the project YAML (SoT), not the log alone.

### 3. Direct YAML / file edits

Hand edits under `~/.jstdata/projects/<id>/` bypass the log entirely.

### 4. Promote-from-event coverage is best-effort

`jst actions promote <event-id>` only succeeds when the event artifact or inputs
carry a query-shaped spec (or resource ids). Pure search/metadata events may
need a follow-up promote `--query` after a query exists another way.

### 5. Cache identity ≠ semantic refresh policy

`--refresh` mints a new artifact; old events keep old artifacts. Reconstructing
“what the agent believed at time T” works per event, but there is no TTL /
invalidation journal explaining *why* a refresh was requested.

### 6. Actor / concurrency attribution is thin

Events may carry an `actor` field, but CLI agents often omit it. You can filter
by `project_id` / `investigation_id`, not reliably by “which agent.”

### 7. Feedback diagnostics ≠ full UI reconstruct

Outbox diagnostics capture screen id, project/investigation, platform, and
redacted extras at submit time. They do not serialize the full TUI widget tree
or chart pixels. Sufficient for triage; not a pixel-perfect replay.

### 8. Preview / publish are projection, not log replay

`jst preview` and legacy `jst publish` read investigation YAML (`in_report`
queries). Action history is intentionally excluded from publications. You cannot
rebuild a published page from the log without the curated query SoT.

### 9. Legacy singleton investigations

Migrated one-tab projects retain history only for ops run *after* migration onto
the project log. Pre-migration CLI/`jst api` work is invisible to the log.

### 10. Soft-deprecated `jst api` surface

Still useful for general-purpose data access; outside the audited research path.
Agents that bypass `jst actions` leave gaps by construction.

## Intentional non-goals (for now)

- Daemonized live tail of another process’s ops
- Replaying the log into a new project as an undo stack
- Remote feedback delivery / retry (Workstream C)
- Multi-tab remote publish wire format (C)

## Suggested follow-ups (not blocking §19.1)

1. Logged `query.run` / shallow observation ops with artifacts.
2. Emit ops (or compact patches) for workspace and view-tree mutations.
3. Require / default `actor` for agent CLI invocations.
4. Richer promote from search+metadata event graphs (compose a query spec).
