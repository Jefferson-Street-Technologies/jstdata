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
   Workspaces live in investigation YAML and are the live objects action screens
   mutate.
2. **Action** — a self-describing interactive TUI that edits the active
   workspace. Actions are order-agnostic: any action must accept an empty or
   arbitrary workspace. Cataloged under `jstdata/actions/`; register via
   `ActionSpec`.
3. **Host** — `InvestigationHost` runs one process over an investigation’s
   action tree. It owns the active workspace + labels cache (hydrated from the
   API; never persisted in the investigation). Universal UI:
   - `:` command mode (`:workspace`, `:search`, `:add`, `:rm`, …)
   - left workspace drawer / bottom search drawer
   - `n` / `p` next / previous action in the tree
   - `N` / `P` next / previous workspace
   - `q` quit
   - `?` action-specific keybindings (from the catalog entry)
4. **Investigation** — named YAML under `~/.jstdata/investigations/` that stores
   **workspaces + action tree** (type, args, parent_id). Managed with
   `jst investigation` (`create` / `load` / `ls`). Action mutations use
   `jst action` (`add` / `rm`). Active investigation / workspace / action ids
   live in `~/.jstdata/.active.yaml`. A bundled tutorial investigation is
   bootstrapped when none exist.

Composition is the **action tree**:

```bash
jst investigation create gdp-rank
jst action add console --args '{"taxonomy":"country"}'
jst action add rank --args '{"taxonomy":"country"}' --activate
jst investigation load gdp-rank
```

Catalog + `ActionSpec` live in `jstdata/actions/`. The host runtime is
`jstdata/investigation/host.py`. Actions register via import-time
`register(ActionSpec)`. Built-in actions today: `console`, `discover`, `rank`.

## Why actions matter

An action is the unit of product surface area. New research UIs should almost
always be **new actions**, not forks of the console.

Because actions share a workspace and the host’s chrome:

- Users can grow an investigation tree without leaving the process.
- Workspace staging and search stay consistent.
- Agents/humans can discover actions via `jst action ls` /
  `jst action show <id> [--json]` without reading TUI code.

Do **not** encode action ordering in metadata. Compatibility is “works with any
workspace,” including empty.

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
   (same pattern as `console` / `discover` / `rank`).
5. **Implement the Textual `Screen`** so it mutates `workspace` only. When adding
   a resource, also call `app.remember_label(id, label)` so host UI can show
   names. Rely on host `:` / drawers for shared workspace/search UI; only declare
   action-local keys on `bindings` and implement matching `action_*` methods.

Seed args go on `arguments`. They configure the TUI via
`jst action add <id> --args '{...}'`; they do not replace interactive use.

## How to use an action

| Goal | Command |
|------|---------|
| List actions | `jst action ls` / `jst action ls --json` |
| Describe one action | `jst action show <id>` / `jst action show <id> --json` |
| Create investigation | `jst investigation create <id>` |
| Open investigation | `jst investigation load <id>` |
| List investigations | `jst investigation ls` |
| Add action | `jst action add console --args '{"taxonomy":"country"}'` |
| Add and activate | `jst action add discover --activate` |
| Remove action | `jst action rm <action-id> --force` |
| Resolve resources | `jst api metric search …` / `jst api entity search …` |
| Query / observations | `jst api query …` / `jst api series observations …` |
| Agent bootstrap | `jst agent-guide` |
| Inside the TUI | `:` commands · `n`/`p` actions · `N`/`P` workspaces · `?` · `q` |

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
