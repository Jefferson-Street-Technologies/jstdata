# §19.1 Client proof of concept — demo script

Dogfood checklist for founders. Complete without Publication Backend (Workstream C)
changes. Prefer a publicly traded company you know (example: **FTAI Aviation**).

## Setup

1. Create a project with three investigations:
   ```bash
   jst new --id ftai-fundamentals --title Fundamentals
   # note the project id from `jst ls` / disk under ~/.jstdata/projects/
   jst new --project <project-id> --title Peers
   jst new --project <project-id> --title Macro
   ```
   Or from the TUI on the first investigation: `:tab new Peers`, `:tab new Macro`.
2. Launch two coding agents, each targeting a different investigation via explicit
   `--project` / `--investigation` on every `jst actions` call.
3. Keep the Macro tab for manual research (`jst open` / `{` `}` / `:tab next`).

## Research

### Fundamentals agent

- [ ] Resolve the company: `jst actions search entities "…" --project … --investigation …`
- [ ] Metadata: `jst actions metadata entity <id> …`
- [ ] Discover metrics: `jst actions search metrics "…" …`
- [ ] Exploratory values: `jst actions query run --metric … --entity … --tail 1 …`
      (optional `--save` for exploratory persistence)
- [ ] Promote selected work: `jst actions promote <event-id> …` (or `--query <id>`)

### Peers agent

- [ ] Relationships: `jst actions relationships <company-id> …`
- [ ] Industry / classification vs membership (use relation filters; do not confuse
      with `--taxonomy` membership)
- [ ] Peer universe via `jst actions search entities --relation …`
- [ ] Metric coverage / comparative exploratory queries
- [ ] Promote selected queries into the Peers report

### Human (Macro)

- [ ] Direct queries without staging when useful
- [ ] Workspace-driven queries when useful
- [ ] Navigate between investigations (`{` / `}` or `:tab next|prev`) while agents work
- [ ] Confirm agents did not steal the active viewport

## Observability

- [ ] Agent ops appear in TUI (`A` / `:activity`) and
      `jst actions log --project … --investigation …`
- [ ] Human viewport remains stable while agents target other tabs
- [ ] Filter log by investigation and project
- [ ] Reopen historical search / metadata / relationships:
      `jst actions inspect <event-id>` (no new Data API call; artifact reused)
- [ ] Failed ops are recorded (`--status error`)

## Curation

- [ ] Exploratory results are **not** in `jst preview` until promoted
- [ ] Promote previously executed / logged work into the report
- [ ] Copy across tabs: `jst actions copy --project … --from … --to …`
- [ ] Duplicate an investigation: `:tab dup`
- [ ] Confirm independent workspace / queries / history per tab

## Feedback

- [ ] From any research page: `:feedback <comment>` (or
      `jst actions feedback submit "…" --project … --investigation … --screen …`)
- [ ] Inspect the outbox: `jst actions feedback ls` /
      `jst actions feedback inspect <id>`
- [ ] Confirm diagnostics capture screen / project / investigation context and redact
      secret-looking keys (remote ingest awaits C)

## Local publication

- [ ] `jst preview` on the multi-tab project
- [ ] Only `in_report` queries appear
- [ ] Each investigation has its own ordered query collection
- [ ] Private workspaces, exploratory artifacts, and action history are excluded
- [ ] `jst publish` on a multi-tab project notifies that remote multi-tab awaits C
      (does not silently flatten)

## Legacy compatibility

- [ ] Existing single-investigation files still load
- [ ] Single-investigation `jst publish` still works
- [ ] `jst clone` still works
- [ ] In-place publication updates remain compatible

## Done when

The scenario above runs end-to-end on the client alone. Capture gaps in
`docs/action_log_gaps.md` and adapter expectations in `docs/adapter_notes_c.md`.
