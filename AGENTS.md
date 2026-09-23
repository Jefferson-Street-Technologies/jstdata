# AGENTS.md — jstdata workflows

Guidance for humans and agents extending the interactive CLI.

For **how to operate** the product as an agent (policy, modes, recipes), run
`jst agent-guide`. This file is for **extending** the codebase.

## Architecture

`jstdata` centers on four ideas:

1. **Session** — portable analytical intent (`metric` / `entity` / `series` +
   `head`/`tail`/`as_of`/`sort_by`/`taxonomy`). Mirrors `JSTDataClient.query`
   parameters. No observations; can execute, export CSV, or render Python/CLI
   snippets. **Source of truth** for what is staged. Deep history uses
   `GET /series/{id}/observations`, not `/query` date windows. There is no
   session CLI yet — write JSON by hand or via `Session.save` / the export modal.
2. **Step** — a self-describing interactive TUI that edits a shared `Session`.
   Steps are order-agnostic: any step must accept an empty or arbitrary session.
3. **Host** — `WorkflowHost` runs one process over a pipeline of steps. It owns
   the session for the lifetime of the run, plus a UI-only `labels: dict[id, str]`
   cache (hydrated from the API; never persisted). Universal UI every step gets:
   - `s` session modal (list / remove / inspect API JSON)
   - `f` find modal (search catalog / add to session / inspect; Shift+Tab toggles
     metrics vs entities)
   - `e` export modal (copy Python, copy CLI, write session JSON)
   - `n` / `p` next / previous step
   - `q` quit
   - `?` step-specific keybindings (from the step’s catalog entry)
4. **Saved workflow** — named YAML under `~/.jstdata/workflows/` that stores
   **step topology + step args only** (not session contents). Managed with
   `jst workflow` (`ls` / `create` / `rm` / `run`). Plural `jst workflows` is a
   compatibility alias. Bake discovered ids into a session JSON and pass
   `--session` when running. Bundled workflows (e.g. `tutorial`) ship in-package
   and are not deletable.

Composition happens in the **shell**, not a custom DSL:

```bash
jst run STEP [ARGS...] : STEP [ARGS...] : ...
jst workflow create --id gdp-rank -- console : rank --taxonomy country
jst workflow run gdp-rank --session labor.json
```

Pipeline tokens for `workflow create` must follow a `--` boundary.

Catalog + parsing live in `jstdata/workflows/`. The host runtime is
`workflows/host.py`; shared modals live beside it (`session_manager.py`,
`find.py`, `export.py`). Steps register via import-time `register(StepSpec)`.
Built-in steps today: `console`, `discover`, `rank`. The coached tutorial is
`jst tutorial` (or `jst workflow run tutorial`).

## Why steps matter

A step is the unit of product surface area. New research workflows should almost
always be **new steps**, not forks of the console.

Because steps share a `Session` and the host’s session/export chrome:

- Users can chain specialized UIs without leaving the process.
- Export and session management stay consistent.
- Agents/humans can discover steps via `jst steps` / `jst step <id> [--json]`
  without reading TUI code.

Do **not** encode step ordering in metadata. Compatibility is “works with any
session,” including empty.

## How to create a step

1. **Add a module** under `jstdata/workflows/` (e.g. `company_selector.py`).
2. **Implement a screen factory**:

   ```python
   def create_my_screen(client, session, **kwargs):
       # kwargs come from StepArgument CLI flags
       return MyScreen(client, session, **kwargs)
   ```

3. **Register a `StepSpec`**:

   ```python
   from .base import StepArgument, StepBinding, StepSpec, register

   MY_STEP = register(
       StepSpec(
           id="my-step",                 # CLI id
           name="My Step",
           description="One sentence.",
           create_screen=create_my_screen,
           arguments=(
               StepArgument(
                   name="industry",
                   type="string",
                   description="Industry filter",
               ),
               # Use multiple=True for repeatable flags (e.g. console --relation)
           ),
           bindings=(
               StepBinding("i", "inspect", "Inspect highlighted item"),
               # Shown under ? and in `jst step my-step`
           ),
           example="jst run my-step --industry semiconductors : console",
       )
   )
   ```

4. **Import the module** from `jstdata/workflows/__init__.py` so it registers
   (same pattern as `console` / `discover` / `rank`).
5. **Implement the Textual `Screen`** so it mutates `session` only. When adding a
   resource, also call `app.remember_label(id, label)` so host UI can show names.
   Rely on host `s` / `f` / `e` for shared session/find/export UI; only declare
   step-local keys on `bindings` and implement matching `action_*` methods.

Seed flags go on `arguments`. They configure the TUI; they do not replace
interactive use. List-valued args round-trip through YAML / `workflow_to_tokens`
as repeated `--flag` values.

## How to use a step

| Goal | Command |
|------|---------|
| List steps | `jst steps` / `jst steps --json` |
| Describe one step | `jst step <id>` / `jst step <id> --json` |
| Run alone | `jst run console` / `jst run discover --mode union` / `jst run rank --taxonomy country` |
| Scope console | `jst run console --taxonomy country --resource-type entity` |
| Relation-scoped console | `jst run console --relation classified_as:sic:3674 --resource-type entity` |
| Rank session entities | `jst run --session eu.json rank` (auto when session has entities) |
| Discover session metrics | `jst run --session eu.json discover` (auto when session has metrics; `--session-metrics=false` for full candidates) |
| Preload session JSON | `jst run --session path.json console` |
| Set default export path | `jst run --output out.json console` |
| Chain steps | `jst run console --taxonomy country : discover : rank --taxonomy country` |
| Save a workflow | `jst workflow create --id gdp-rank -- console : rank --taxonomy country` |
| Run a workflow | `jst workflow run gdp-rank --session labor.json` |
| Tutorial | `jst tutorial` |
| Agent bootstrap | `jst agent-guide` |
| Inside the TUI | `s` session · `f` find · `e` export · `n`/`p` navigate · `?` step keys · `q` quit |

There is **no in-UI “load session” picker**. To reload, quit, recall the command
from shell history, and add `--session`.

Session writes happen from the export modal (`e`). The host picks a unique
default filename unless `--output` is set.

## Catalog / search notes (for step authors)

- Prefer `client.search_entities` / `search_metrics` with optional query (omit query
  to list a filtered set). Support `--taxonomy`, repeatable anchors, and
  `--mode union|intersect` where the API does.
- Entity search also accepts repeatable `--relation` values
  (`<relationship_type>:<to_entity_id>`, OR'd). Split on the **first** colon so
  ids like `sic:3674` stay intact. Types: `equivalent_to`, `classified_as`,
  `part_of`, `has_security` (not `identity` — use taxonomy for membership).
- Taxonomy membership for query/rank is the population bound (`--taxonomy`), not
  a generic graph filter. Rank scopes to session entity ids on `/query` by
  default when the session has entities; use `--session-entities=false` to rank
  the full taxonomy/all population instead.
