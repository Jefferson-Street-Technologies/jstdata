# AGENTS.md — jstdata investigations

Guidance for humans and agents **extending** the interactive CLI.

For **how to operate** the product as an agent (policy, modes, recipes), run
`jst agent-guide`. This file is for **extending** the codebase.

## Architecture

`jstdata` centers on four ideas:

1. **Workspace** — staged analytical intent inside an investigation (`metric` /
   `entity` / `series` + `head`/`tail`/`as_of`/`sort_by`/`taxonomy`). Mirrors
   `JSTDataClient.query` / `jst api query` parameters. No observations. Deep
   history uses `GET /series/{id}/observations`, not `/query` date windows.
   The investigation has **one** workspace (the live object action screens
   mutate). Saved **queries** are separate YAML objects: cartesian
   metric × entity (plus optional series) sent to `GET /query`.
2. **Action / View** — a self-describing interactive TUI. Staging actions
   (`console`, `discover`, `rank`) grow the active workspace. Read/drill views
   (`table`, `metric`, `entity`, `metrics`) take an **Input Context** (workspace
   plus optional per-node `context` overrides) and must accept empty or arbitrary
   material. Cataloged under `jstdata/actions/`; register via `ActionSpec`.
3. **Host** — `InvestigationHost` runs one process over an investigation’s
   action tree. It owns the active workspace, resolved Input Context, + labels
   cache (hydrated from the API; never persisted in the investigation).
   Universal UI:
   - `g` Home (five destinations: Recent, Search, Actions, Workspace, Queries)
   - `esc` one navigation level back
   - `:` command palette (fuzzy destinations / queries + existing `:workspace`,
     `:search`, `:add`, `:rm`, `:query` grammar)
   - left workspace drawer / bottom search drawer
   - `n` / `p` (or ↑/↓) next / previous sibling action (action views)
   - `N` / `P` (or →/←) into first child / up to parent
   - Workspace destination / drawer: `j`/`k` rows, backspace to remove
   - `q` quit
   - `?` action-specific keybindings (from the catalog entry)
4. **Investigation** — named YAML under `~/.jstdata/investigations/` that stores
   **one workspace + queries + action tree** (type, args, parent_id, optional
   `context`). Managed with `jst new` / `jst ls` / `jst open`; bare `jst`
   resumes the active investigation (creates Untitled if none). Saved queries
   use `jst query` (`ls` / `show` / `inspect` / `run` / `add`). Action mutations
   use `jst action` (`add` / `rm`). Active investigation / workspace / action ids
   live in `~/.jstdata/.active.yaml`. Bundled `tutorial` and `plain-bagel` are
   installed on first use.

Composition is the **action tree**:

```bash
jst new --id gdp-rank
jst action add table --args '{"sort_by":"value"}' --activate
jst action add rank --args '{"taxonomy":"country"}'
jst query add --from-workspace --name "workspace snapshot"
jst open gdp-rank
```

Catalog + `ActionSpec` live in `jstdata/actions/`. The host runtime is
`jstdata/investigation/host.py`. Actions register via import-time
`register(ActionSpec)`. Built-ins: views `table` / `metric` / `entity` /
`metrics` / `release`; staging/explore `console` / `discover` / `rank`.
Bundled demo: `plain-bagel` (hyperscaler debt + release provenance).

## Why actions matter

An action is the unit of product surface area. New research UIs should almost
always be **new actions**, not forks of console or table.

Because actions share a workspace (and views share resolved Input Context) plus
the host’s chrome:

- Users can grow an investigation tree without leaving the process.
- Workspace staging and search stay consistent; drills open child views with
  tighter `context` without mutating the global bag.
- Agents/humans can discover actions via `jst action ls` /
  `jst action show <id> [--json]` without reading TUI code.

Do **not** encode action ordering in metadata. Compatibility is “works with any
workspace / Input Context,” including empty.

## How to create an action

1. **Add a module** under `jstdata/actions/` (e.g. `company_selector.py`).
2. **Implement a screen factory**:

   ```python
   def create_my_screen(client, workspace, **kwargs):
       # kwargs come from ActionArgument / jst action add --args JSON
       return MyScreen(client, workspace, **kwargs)
   ```

3. **Register an `ActionSpec`**:

   ```python
   from .base import ActionArgument, ActionBinding, ActionSpec, register

   MY_ACTION = register(
       ActionSpec(
           id="my-action",               # CLI / YAML type id
           name="My Action",
           description="One sentence.",
           create_screen=create_my_screen,
           arguments=(
               ActionArgument(
                   name="industry",
                   type="string",
                   description="Industry filter",
               ),
               # Use multiple=True for list-valued seed args
           ),
           bindings=(
               ActionBinding("i", "inspect", "Inspect highlighted item"),
               # Shown under ? and in `jst action show my-action`
           ),
           example=(
               "jst action add my-action --args "
               "'{\"industry\":\"semiconductors\"}'"
           ),
       )
   )
   ```

4. **Import the module** from `jstdata/actions/__init__.py` so it registers
   (same pattern as `table` / `console` / `rank`).
5. **Implement the Textual `Screen`**. Staging actions mutate `workspace`; views
   read `input_context` (and may call `app.open_child_view` with
   `focus_replace_context`). When adding a resource, also call
   `app.remember_label(id, label)`. Rely on host `:` / drawers for shared
   workspace/search UI; only declare action-local keys on `bindings`.

Seed args go on `arguments`. They configure the TUI via
`jst action add <id> --args '{...}'`; they do not replace interactive use.

## How to use an action

| Goal | Command |
|------|---------|
| List actions | `jst action ls` / `jst action ls --json` |
| Describe one action | `jst action show <id>` / `jst action show <id> --json` |
| Create investigation | `jst new` (optional `--id` / `--title`) |
| Open investigation | `jst open <id>` |
| Resume active | `jst` |
| List investigations | `jst ls` |
| Saved queries | `jst query ls` / `show` / `inspect` / `run` / `add` |
| Add view | `jst action add table --args '{"sort_by":"value"}'` |
| Add and activate | `jst action add metrics --args '{"mode":"intersect"}' --activate` |
| Remove action | `jst action rm <action-id> --force` |
| Resolve resources | `jst api metric search …` / `jst api entity search …` |
| Query / observations | `jst api query …` / `jst api series observations …` |
| Agent bootstrap | `jst agent-guide` |
| Inside the TUI | `g` home · `esc` back · `:` palette · `n`/`p` siblings · `N`/`P` depth · `:workspace` · space collect · `?` · `q` |

## Catalog / search notes (for action authors)

- Prefer `client.search_entities` / `search_metrics` with optional query (omit query
  to list a filtered set). Support taxonomy, repeatable anchors, and
  `mode union|intersect` where the API does.
- Entity search also accepts repeatable relation values
  (`<relationship_type>:<to_entity_id>`, OR'd). Split on the **first** colon so
  ids like `sic:3674` stay intact. Types: `equivalent_to`, `classified_as`,
  `part_of`, `has_security` (not `identity` — use taxonomy for membership).
- Taxonomy membership for query/rank is the population bound (`taxonomy`), not
  a generic graph filter. Rank scopes to workspace entity ids on `/query` by
  default when the workspace has entities; use `workspace_entities=false` in
  `--args` to rank the full taxonomy/all population instead.
