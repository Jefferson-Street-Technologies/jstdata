"""Generate the markdown agent bootstrap guide (`jst agent-guide`).

CLI commands and action catalog entries are derived from the live Click tree
and action registry. Conceptual glue (policy, modes, recipes) is static prose.
"""

from __future__ import annotations

import re
from dataclasses import fields
from importlib.metadata import PackageNotFoundError, version
from typing import Iterable, get_args, get_origin

import click

from .investigation.model import Workspace
from .actions import list_actions

_PACKAGE = "jstdata"

_WORKSPACE_FIELDS_OMIT = frozenset(
    {"id", "label", "description", "start_date", "end_date", "start_time", "end_time"}
)

_HARD_RULES = """\
## Hard rules

- **Never invent IDs.** Never invent metric, entity, taxonomy, or series ids from
  English names. Resolve via `jst api … search` (or scoped list) first.
- **Resolve before use.** Resolve ids before `jst api query`, investigation
  workspaces, saved queries, or action `--args`.
- **Use `--format json`** on `jst api` resource and search commands unless the user
  needs a table.
- **Do not launch interactive TUIs** (`jst` / `jst new` / `jst open`) unless the
  user explicitly asks you to operate the UI. Prefer CLI mutation of the active
  investigation (`jst action add` / `rm`, `jst query add` / `run`) or hand them
  `jst open <id>`.
- **Do not paginate exhaustively.** During discovery, use `--limit` 20–50. Do not walk
  `offset` through the catalog unless the returned set is clearly insufficient.
- **Do not enumerate** `jst api metric ls`, `jst api series ls`, or full taxonomy
  populations unless the task requires enumeration. Prefer `search` with a query.
- **Do not retrieve deep history** until the candidate universe is narrowed. Use
  `tail=1` (or similarly shallow queries) for triage.
- **Prefer investigations over raw API loops** when the user will continue the work
  interactively. Stage work into the investigation, then hand off.
- **Stop when good enough.** Prefer a partial useful result over open-ended search.
"""

_OPERATING_POLICY = """\
## Default operating policy

When solving a JST research task:

1. **Use metadata before observations.** Resolve entities, metrics, and taxonomies
   before querying values.
2. **Start narrow.** Use the user's literal terminology first, then a small number of
   obvious synonyms.
3. **Prefer bounded calls.** Small result limits during discovery; shallow `tail` /
   `head` when values are needed only to triage.
4. **Prefer good-enough candidate sets** over exhaustive search unless the user asks
   for comprehensiveness.
5. **Escalate to deep history only after narrowing** (specific series ids, date bounds).
6. **Hand off TUIs to the human.** Prepare or mutate an investigation; print
   `jst open <id>` for them.
7. **Prefer JSON** for anything you will parse or reason over.
"""

_OPERATING_MODES = """\
## Choose an operating mode

### Catalog discovery

Example: “Find metrics relevant to defense spending.”

Use `jst api … search` / `show`. Do **not** retrieve observations unless needed to
disambiguate candidates. Select a useful set and stop.

### Agent analysis

Example: “Compare military expenditure as a share of GDP across NATO countries.”

Resolve ids → bounded `jst api query` → deeper `jst api series observations` only if
needed → answer from structured CLI output. No TUI required.

### Human handoff

Example: “Set me up to explore defense spending across Europe.”

Resolve resources → create/open an investigation → `jst action add` actions
and/or `jst query add` → hand the user `jst open <id>`.
Do not substitute endless search for completing the handoff.
"""

_DATA_MODEL = """\
## Data model

- **Metric** — a measurable theme (e.g. GDP, employed-persons). Identified by a slug id.
- **Entity** — a context the metric is measured in (e.g. a country, a company). Slug id.
- **Taxonomy** — a named population of entities (e.g. `country`, `us-county`,
  `sec-central-index-key`). Use it to scope search, query, and ranking.
- **Series** — one concrete time series: a metric observed for an entity (at a frequency).
- **Investigation** — the durable research artifact: title, notes, one workspace,
  saved queries, and an action tree.
- **Workspace** — the **global** bag of staged resource ids inside an
  investigation (no observations). Mutated by `:search` / `:workspace` and staging
  actions; never mutated by view drills.
- **Query** — a saved `GET /query` spec. Metric × entity is a **cartesian product**
  (every metric against every entity), plus optional `series`. Filters such as
  frequency / head / tail live here. Inspect coverage with `jst query inspect`
  (no observations); execute with `jst query run`.
- **Action / View** — one use of a reusable analytical UI in the investigation tree.
  Read/drill views (`table`, `metric`, `entity`, `metrics`) take an **Input Context**
  resolved from the workspace plus optional per-node `context` overrides. Staging /
  explore actions (`console`, `discover`, `rank`) still grow the global workspace.
- **Input Context** — what a view is *about*: workspace resources with optional
  focus/replace overrides stored on the action node as YAML `context` (metric /
  entity / series / taxonomy). Empty override dimensions inherit from the workspace.
  Child drills use focus/replace; they do **not** rewrite the global workspace.

Ids are opaque slugs. Display names are not ids.
"""

_RESOLUTION = """\
## Resolution

Never invent metric, entity, taxonomy, or series ids from English names.

Always resolve via `jst api … search` (prefer a query string) before writing ids into
an investigation workspace or `jst api query` flags. Scope with `--taxonomy` /
`--relation` when the user names a population or relationship. Confirm ambiguous hits
with `show` before use.
"""

_RECIPES = """\
## Common recipes

### Find relevant metrics

```bash
jst api metric search "defense spending" --taxonomy country --limit 20 --format json
```

If insufficient, try one or two synonyms the same way. Inspect only promising ids:

```bash
jst api metric show <resolved-id> --format json
```

Stop once a useful candidate set exists. Do not page the full catalog.

### Compare latest values

```bash
jst api query \\
  --metric <metric-id> \\
  --taxonomy country \\
  --tail 1 \\
  --sort-by value \\
  --limit 50 \\
  --format json
```

### Retrieve history after narrowing

```bash
jst api metric series <metric-id> --format json
jst api series observations <series-id> \\
  --start-date 2015-01-01 \\
  --format json
```

### Rank GDP among EU countries

1. Resolve the EU entity, then list members via relation (do not invent country ids):

```bash
jst api entity search "european union" --limit 10 --format json
jst api entity search --relation part_of:<eu-entity-id> --taxonomy country \\
  --limit 50 --format json
```

2. Resolve a GDP metric for the country population:

```bash
jst api metric search gdp --taxonomy country --limit 20 --format json
jst api metric show <gdp-metric-id> --format json
```

3. Create or load an investigation and stage metric + member entity ids into the
   active workspace (edit investigation YAML, or guide the human to `:search` /
   `:workspace` in the TUI).

4. Compare or rank via API, or hand off a view / rank action:

```bash
jst api query \\
  --metric <gdp-metric-id> \\
  --entity <fr> --entity <de> --entity <it> \\
  --tail 1 \\
  --sort-by value \\
  --format json

# Population leaderboard (stages entities back into the workspace)
jst action add rank --args '{"taxonomy":"country"}' --activate

# Or a latest-value matrix over the staged workspace (human drills with Enter)
jst action add table --args '{"sort_by":"value"}' --activate
```

Human handoff:

```bash
jst open <investigation-id>
```

### Prepare a human investigation

1. Resolve ids via `jst api … search`.
2. `jst new` (or `jst open <id>` / mutate with `jst action add` / `jst query add`).
3. Prefer a **view** for read/drill (`table`, `metric`, `entity`, `metrics`) and
   keep `console` / `discover` / `rank` when the human still needs to stage or
   explore the global workspace. Prefer `jst query add` for a cartesian pull.
4. Hand them: `jst open <id>`.

Do not substitute extra research for completing the requested handoff.
"""

_COMPOSITION = """\
## Investigations and actions

Research is an **investigation**: a tree of **actions / views** over one
**workspace**, plus saved **queries** (cartesian metric × entity).

```bash
jst new --id housing-slowdown
jst open housing-slowdown
jst ls
jst query add --metric gdp --entity france --entity usa --name "FR vs US"
jst query inspect 1
jst query run 1 --format json
jst action add table --args '{"sort_by":"value"}' --activate
jst action add query --args '{"query_id":1}' --activate
jst action add metrics --args '{"mode":"intersect"}'
jst action add rank --args '{"taxonomy":"country"}'
jst action rm 2 --force
```

- **Actions** are reusable TUI screens (`jst action ls` / `jst action show <id>`).
- Seed args go in `--args` JSON on `action add` (or `:add` in the TUI).
- Optional per-node **Input Context** overrides live under YAML `context` on the
  action (focus/replace relative to the active workspace). Views resolve this as
  Input Context; drills open child views with tighter context and do not mutate
  the global workspace.
- New actions are children of the active action unless `--parent` is set.
- Humans use `:` in the TUI (`:workspace`, `:search metric gdp`, `:add`, `:rm`,
  `:rename`). The palette also fuzzy-matches destination names and saved query
  labels. `:rename` sets the investigation title and description in one command.
  Tree keys: `g` home, `esc` back, `n`/`p` siblings, `N`/`P` depth (arrows too).
  `:workspace` still opens the bag drawer. Views are typically idle until `r`
  (load). Space collects the focused resource into the workspace. Home has
  five destinations: Recent, Search, Actions, Workspace, Queries.
- **When to use which:**
  - `table` — latest-value matrix; cheap `/preview` coverage on open; `r` runs
    `/query`; Enter drills to entity, metric, or **release** (provenance).
  - `metric` / `entity` — inspect one resource (metric includes description /
    variants / taxonomies); Enter cross-drills.
  - `release` — filing / release behind an observation (`context.release`).
  - `metrics` — metrics associated with an entity set (`mode` union|intersect).
  - `console` / `:search` — catalog search that stages into the global workspace.
  - `discover` — candidate metrics with preview chart; Space stages metrics.
  - `rank` — single-metric population leaderboard; stages entities back.
- Demo investigation: `jst open plain-bagel` (hyperscaler debt + provenance
  loop; query 1 is the cartesian latest-value pull).
- Agents prefer CLI mutation + `jst api …` for resolution; do not drive the TUI.

Active context lives in `~/.jstdata/.active.yaml` (investigation, workspace id, action id).
"""

_WORKSPACE_HOWTO = """\
### Workspace contents

Each investigation has a single `workspace:` mapping (numeric `id` default `0`),
optional `label` / `description`, and resource id lists. Query filters
(frequency, head/tail, units, …) belong on `queries:`, not the workspace bag.

Legacy files with `workspaces:` (a list) load id `0` / the first entry and ignore
the rest.

Agents typically edit `~/.jstdata/investigations/<id>.yaml` or ask the human to stage
via `:search` / `:workspace`. Prefer resolved slug ids only.
"""

def package_version() -> str:
    try:
        return version(_PACKAGE)
    except PackageNotFoundError:
        return "unknown"


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    line = text.strip().splitlines()[0].strip()
    # Click often embeds ``\\b`` blocks; collapse leftover whitespace.
    return re.sub(r"\s+", " ", line)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _param_token(param: click.Parameter) -> str | None:
    """Compact signature fragment for one Click parameter."""
    if getattr(param, "hidden", False):
        return None
    if isinstance(param, click.Argument):
        name = (param.metavar or param.name or "ARG").upper()
        if param.nargs == -1:
            name = f"{name}..."
        return name if param.required else f"[{name}]"
    if isinstance(param, click.Option):
        # Prefer the long form.
        opt = next((o for o in param.opts if o.startswith("--")), None)
        if opt is None:
            opt = param.opts[0] if param.opts else f"--{param.name}"
        if param.is_flag or param.count:
            return opt if param.required else f"[{opt}]"
        meta = (param.metavar or param.name or "VALUE").upper()
        token = f"{opt} {meta}"
        return token if param.required else f"[{token}]"
    return None


def format_command_params(cmd: click.Command) -> str:
    parts: list[str] = []
    for param in cmd.params:
        token = _param_token(param)
        if token:
            parts.append(token)
    return " ".join(parts)


def iter_leaf_commands(
    root: click.Group,
    *,
    prog: str = "jst",
) -> Iterable[tuple[str, click.Command]]:
    """Yield ``(invocation, command)`` for every leaf command under ``root``."""

    def walk(cmd: click.Command, parts: list[str], ctx: click.Context) -> Iterable[tuple[str, click.Command]]:
        if isinstance(cmd, click.Group):
            seen: set[int] = set()
            for name in sorted(cmd.list_commands(ctx)):
                sub = cmd.get_command(ctx, name)
                if sub is None:
                    continue
                # Skip Click aliases that point at the same command object.
                sub_id = id(sub)
                if sub_id in seen:
                    continue
                seen.add(sub_id)
                sub_ctx = click.Context(sub, info_name=name, parent=ctx)
                yield from walk(sub, parts + [name], sub_ctx)
            return
        invocation = " ".join(parts)
        yield invocation, cmd

    root_ctx = click.Context(root, info_name=prog)
    yield from walk(root, [prog], root_ctx)


def render_commands_table(root: click.Group) -> str:
    lines = [
        "| Command | Description | Parameters |",
        "|---------|-------------|------------|",
    ]
    for invocation, cmd in iter_leaf_commands(root):
        desc = _first_line(cmd.help) or _first_line(cmd.short_help) or ""
        params = format_command_params(cmd)
        lines.append(
            f"| `{_md_cell(invocation)}` | {_md_cell(desc)} | {_md_cell(params)} |"
        )
    return "\n".join(lines)


def _format_action_args(spec) -> str:
    if not spec.arguments:
        return "_(none)_"
    bits: list[str] = []
    for arg in spec.arguments:
        flag = arg.flag()
        piece = f"`{flag}`"
        if arg.multiple:
            piece += " (repeatable)"
        if arg.required:
            piece += " (required)"
        elif arg.default is not None:
            piece += f" [default: `{arg.default}`]"
        if arg.choices:
            choices = ", ".join(f"`{c}`" for c in arg.choices)
            piece += f" {{{choices}}}"
        bits.append(piece)
    return ", ".join(bits)


def _annotation_label(annotation: object) -> str:
    """Human-readable type for a workspace field annotation."""
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        inner = _annotation_label(args[0]) if args else "any"
        return f"list[{inner}]"
    if origin is not None:
        # Optional[T] / Union[T, None]
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return f"{_annotation_label(args[0])} | null"
    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if isinstance(annotation, type):
        return annotation.__name__
    text = str(annotation)
    return text.replace("typing.", "").replace("None", "null")



_WORKSPACE_FIELD_NOTES: dict[str, str] = {
    "metric": "Resolved metric slug ids staged for the investigation",
    "entity": "Resolved entity slug ids",
    "series": "Resolved series slug ids (when targeting series directly)",
    "frequency": "Optional frequency filter (Annual, Quarterly, Monthly, Daily, Intraday)",
    "taxonomy": "Restrict analysis to entities in this population (taxonomy slug)",
    "head": "Earliest N observations per series for `/query`",
    "tail": "Latest N observations per series for `/query`",
    "as_of": "Timezone-aware ISO-8601 cutoff on release_timestamp",
    "sort_by": "`id` (default) or `value` (desc) for `/query` ordering",
    "order_by": "Observation order preference where applicable (`asc`/`desc`)",
}


def render_workspace_section() -> str:
    """Workspaces how-to plus a field table derived from ``Workspace``."""
    lines = [
        "## Workspaces",
        "",
        "A **workspace** is the investigation's single global bag of resource ids.",
        "Views also see an **Input Context**: workspace plus optional action `context`",
        "overrides (focus/replace). Saved **queries** are the cartesian `/query` specs;",
        "the workspace itself is a basket. No observations are stored.",
        "",
        "`discover` defaults to workspace metrics when any are staged",
        "(`workspace_metrics=false` in `--args` for the full entity-anchored search);",
        "`rank` defaults to workspace entities when any are staged",
        "(`workspace_entities=false` to rank the full taxonomy/all population).",
        "`metrics` takes `mode` `union`|`intersect` over the Input Context entity set.",
        "`table` takes optional `sort_by` `id`|`value` for the latest-value matrix.",
        "",
        "### Workspace resource fields",
        "",
        "Primary fields inside each investigation workspace (from the installed",
        "`Workspace` model). Prefer `head` / `tail` / `as_of` for query windows.",
        "Omit unused fields.",
        "",
        "| Field | Type | Notes |",
        "|-------|------|-------|",
    ]
    for f in fields(Workspace):
        if f.name in _WORKSPACE_FIELDS_OMIT:
            continue
        note = _WORKSPACE_FIELD_NOTES.get(f.name, "")
        lines.append(
            f"| `{_md_cell(f.name)}` | {_md_cell(_annotation_label(f.type))} | {_md_cell(note)} |"
        )
    lines.append("")
    lines.append(_WORKSPACE_HOWTO.rstrip())
    return "\n".join(lines)


def render_actions_section() -> str:
    specs = list_actions()
    lines = [
        "## Actions",
        "",
        "Reusable analytical UIs instantiated inside investigations. Prefer **views**",
        "(`table`, `metric`, `entity`, `metrics`) for read/drill over Input Context;",
        "keep `console` / `discover` / `rank` when the human still needs to stage or",
        "explore the global workspace. Do not operate these UIs yourself; use",
        "`jst action show <id>` if you need details while helping a human. Seed them",
        "with `jst action add <id> --args '{...}'`.",
        "",
        "| Id | Name | Description | Arguments | Example |",
        "|----|------|-------------|-----------|---------|",
    ]
    if not specs:
        lines.append("| _(none registered)_ | | | | |")
        return "\n".join(lines)

    for spec in specs:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_md_cell(spec.id)}`",
                    _md_cell(spec.name),
                    _md_cell(spec.description),
                    _md_cell(_format_action_args(spec)),
                    f"`{_md_cell(spec.example or f'jst action add {spec.id}')}`",
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("### Action details")
    for spec in specs:
        lines.append("")
        lines.append(f"#### `{spec.id}` — {spec.name}")
        lines.append("")
        lines.append(spec.description.strip())
        lines.append("")
        if spec.arguments:
            lines.append("Seed arguments (JSON keys for `--args`):")
            lines.append("")
            for arg in spec.arguments:
                req = "required" if arg.required else "optional"
                default = (
                    f", default `{arg.default}`" if arg.default is not None else ""
                )
                multi = ", repeatable" if arg.multiple else ""
                lines.append(
                    f"- `{arg.name}` ({arg.type}, {req}{multi}{default}): "
                    f"{arg.description}"
                )
                if arg.choices:
                    lines.append(
                        f"  - Choices: {', '.join(f'`{c}`' for c in arg.choices)}"
                    )
            lines.append("")
        example = spec.example or f"jst action add {spec.id}"
        lines.append(f"Example: `{example}`")
    return "\n".join(lines)


def render_agent_guide(cli_group: click.Group) -> str:
    """Build the full markdown guide for the given Click root group."""
    ver = package_version()
    sections = [
        f"# jstdata agent guide (v{ver})",
        "",
        "Operating manual for agents helping a user with this installed CLI. "
        "Prefer this document over guessing command shapes or resource ids. "
        "Follow **Hard rules** and **Choose an operating mode** before browsing the "
        "full command tables.",
        "",
        _HARD_RULES.rstrip(),
        "",
        _OPERATING_POLICY.rstrip(),
        "",
        _OPERATING_MODES.rstrip(),
        "",
        _DATA_MODEL.rstrip(),
        "",
        _RESOLUTION.rstrip(),
        "",
        _RECIPES.rstrip(),
        "",
        _COMPOSITION.rstrip(),
        "",
        render_workspace_section(),
        "",
        render_actions_section(),
        "",
        "## CLI commands",
        "",
        "One row per leaf command, derived from the installed CLI. Prefer the recipes "
        "and hard rules above; use this table to confirm flags, not as a checklist to "
        "execute. Low-level resource commands live under `jst api`.",
        "",
        render_commands_table(cli_group),
        "",
    ]
    return "\n".join(sections)
