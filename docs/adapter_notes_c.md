# Adapter notes for Workstream C (Publication Backend)

Client-side contracts C should preserve or extend. B dogfoods §19.1 without these
backend changes; C lands multi-tab publish/clone + feedback ingest after founder
approval.

## Multi-tab projects

- On-disk: `~/.jstdata/projects/<project-id>/` with ordered investigation tabs.
- Local preview: `jst preview` builds a multi-tab HTML projection via
  `build_project_preview_model` — **only** queries with `Query.in_report`,
  excludes workspaces, exploratory artifacts, and action history.
- Remote: `jst publish` on a multi-tab project **refuses to silently flatten** and
  notifies that multi-tab remote publish awaits C. Do not change this client guard
  to flatten as a workaround.

## Report membership

- Explicit boolean `Query.in_report` (default false for new `jst actions query add`
  / `--save`; legacy YAML loads default True).
- Set by `jst actions promote` (from event id or `--query <id>`).
- Copy across investigations preserves `in_report` on copied queries.
- **Local preview** (`jst preview`) remains curated/`in_report`-only.
- **Remote publish** (handoff §1.7): default **includes** workspace + view-tree
  `actions`; only `in_report` queries; `--exclude-workspace` / `--exclude-actions`;
  auto-omit workspace then actions on 256KB overrun with user notification; then
  error. Action **log** / SQLite artifacts never publish.

## Legacy single-investigation wire

- Existing publish/clone payloads for one investigation remain the compatibility
  baseline (`jstdata/publish.py`).
- Do not break legacy round-trips when adding multi-tab.

## Suggested multi-tab publish shape (client expectation)

C should accept a project-level definition roughly:

- Ordered list of investigations (tab id, title, description)
- Per investigation: curated `in_report` queries (+ annotations / `notes_on` if
  supported), **not** private workspace bags, **not** action-log history
- Stable project id ↔ publication id mapping for updates
- Clone returns a **project** directory (multi-tab), not a flattened singleton

Exact JSON schema is C’s call; client will grow an encoder once the backend lands.

## Feedback outbox → remote ingest

Local SQLite table `feedback_outbox` (`jstdata/ops/feedback.py`):

| Field | Notes |
|-------|--------|
| `feedback_id` | `fb_<hex>` |
| `created_at` | UTC ISO |
| `status` | `queued` (local); C should drive `submitted` / `failed` / retry |
| `message` | User text |
| `project_id` / `investigation_id` / `screen` | Targeting |
| `diagnostics_json` | Redacted bundle (platform, screen, extras); secret-like keys already scrubbed |

Client CLI: `jst actions feedback submit|ls|inspect`. TUI: `:feedback <message>`.

C needs: authenticated ingest, validation, durable store, retry of failed delivery.
Client will add a flush/retry command once the endpoint exists — do not require
secrets in the outbox.

## Action log (not published)

- `state.sqlite` + artifact files under the project (or app-dir store) are **private
  working state**. Never include them in publications or clones meant for the web.
- Historical inspect is local-only (`jst actions inspect`).

## Compatibility checklist for C

- [ ] Legacy single-inv publish / clone / in-place update still pass client tests
- [ ] Multi-tab publish preserves tab order and per-tab curated queries
- [ ] Private workspace + action history excluded from public payloads
- [ ] Feedback ingest accepts outbox-shaped diagnostics; supports retry
- [ ] Clone reconstitutes a local multi-tab project loadable by `jst ls` / `jst open`
