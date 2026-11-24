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
  English names. Resolve via `jst actions search …` first.
- **Prefer `jst actions`.** Shared research ops for humans and agents. Soft-deprecated
  `jst api` remains for low-level / exploratory value pulls; retired `jst action` /
  `jst query` groups print pointers and must not be used.
- **Resolve → act → inspect → promote → hand off.** Resolve ids, run logged actions,
  reopen artifacts with `jst actions inspect`, promote into the report, then hand the
  human a project (`jst open` / `jst ls`).
- **Target explicitly.** Pass `--project` and `--investigation` on mutating
  `jst actions` commands. Do not rely on ambient `.active` when multiple agents share
  a project.
- **Default JSON.** `jst actions` defaults to `--format json`. Prefer JSON for anything
  you parse.
- **Do not launch interactive TUIs** (`jst` / `jst new` / `jst open`) unless the user
  explicitly asks you to operate the UI. Create tabs with `jst new --project …`
  only when requested, or tell the human to open the project.
- **Do not edit project / investigation YAML.** SoT lives under
  `~/.jstdata/projects/<id>/`. Read files only to inspect. Mutate via `jst actions`
  (promote, copy, search, metadata, …) or the TUI. Never invent query ids or rewrite
  `workspace:` / `queries:` / `actions:` by hand.
- **Exploratory by default.** Logged search/metadata does not enter the published
  report. Report membership is explicit via `jst actions promote`.
- **Do not paginate exhaustively.** During discovery, use `--limit` 20–50. Do not walk
  `offset` through the catalog unless the returned set is clearly insufficient.
- **Do not enumerate** full taxonomy populations unless the task requires it. Prefer
  `jst actions search …` with a query and relation / taxonomy filters.
- **Stop when good enough.** Prefer a partial useful result over open-ended search.
"""

_OPERATING_POLICY = """\
## Default operating policy

When solving a JST research task:

1. **Resolve ids first** with `jst actions search entities|metrics` (and taxonomy /
   relation filters when the user names a population).
2. **Fetch metadata / relationships** with `jst actions metadata …` and
   `jst actions relationships` before pulling values.
3. **Keep calls bounded.** Small `--limit` during discovery; shallow value pulls only
   to triage.
4. **Log is the audit trail.** Every `jst actions` op returns an `event_id`. Reopen
   with `jst actions inspect <event-id>` (no Data API re-fetch). List with
   `jst actions log`.
5. **Promote deliberately.** `jst actions promote <event-id>` or
   `jst actions promote --query <id>` marks report membership (`in_report`).
6. **Hand off the project**, not raw API dumps. Print `jst open` / `jst ls` for the
   human. Do not drive their TUI viewport.
7. **Prefer JSON** for anything you will parse or reason over.
"""

_OPERATING_MODES = """\
## Choose an operating mode

### Catalog discovery

Example: “Find metrics relevant to defense spending.”

Use `jst actions search metrics` / `jst actions metadata metric`. Do **not** retrieve
observations unless needed to disambiguate. Select a useful set and stop.

### Agent analysis

Example: “Compare military expenditure as a share of GDP across NATO countries.”

Resolve ids → relationships / taxonomy membership → bounded exploratory values
(`jst actions query run … --tail 1`) → answer from structured output. Promote only
what belongs in the report. No TUI required.

### Human handoff

Example: “Set me up to explore defense spending across Europe.”

Resolve resources against a project investigation → leave promoted report queries and
a clear handoff (`jst open` / project id). Do not substitute endless search for
completing the handoff.
"""

_DATA_MODEL = """\
## Data model

- **Project** — directory under `~/.jstdata/projects/<id>/` with ordered investigation
  tabs, shared action log (`state.sqlite` + artifacts), and batch-written YAML.
- **Metric** — a measurable theme (e.g. GDP, employed-persons). Identified by a slug id.
- **Entity** — a context the metric is measured in (e.g. a country, a company). Slug id.
- **Taxonomy** — a named population of entities (e.g. `country`, `us-county`,
  `sec-central-index-key`). Membership ≠ relationship classification.
- **Series** — one concrete time series: a metric observed for an entity (at a frequency).
- **Investigation** — one tab: title, notes, annotations, one workspace, saved queries,
  and an action / view tree. Independent of sibling tabs.
- **Workspace** — the **global** bag of staged resource ids inside an investigation
  (no observations). Mutated by TUI staging; never mutated by view drills.
- **Query** — a `GET /query` spec (cartesian metric × entity, optional series).
  Ladder: **Draft** (`in_report=false`) → **Saved** (`in_report=true`, publication-
  eligible). Direct/incremental exploration creates drafts; `s` / `jst actions
  promote` saves. Local preview stays saved-only.
- **Action log / artifact** — every `jst actions` op appends an event; cacheable ops
  store an immutable artifact. Inspect historical payloads without another API call.
- **Action / View** — reusable TUI screens in the investigation tree (`table`,
  `metric`, `entity`, …). Agents prefer `jst actions` over seeding views via CLI.
- **Input Context** — what a view is *about*: workspace resources with optional
  per-node `context` overrides. Child drills do **not** rewrite the global workspace.

Ids are opaque slugs. Display names are not ids.
"""

_RESOLUTION = """\
## Resolution

Never invent metric, entity, taxonomy, or series ids from English names.

Always resolve via `jst actions search …` before writing ids into a workspace,
promote payload, or exploratory `jst api query`. Scope with `--taxonomy` /
`--relation` / `--relation-direction` / `--relation-taxonomy` when the user names a
population or relationship. Confirm ambiguous hits with
`jst actions metadata entity|metric` before use. Pass `--refresh` only when you must
bypass the local artifact cache.
"""

_RECIPES = """\
## Common recipes

Assume a project id `PROJ` and investigation id `INV` (pass them on every mutating
call). Replace placeholders with resolved slug ids.

### Find relevant metrics

```bash
jst actions search metrics "defense spending" --taxonomy country --limit 20 \\
  --project PROJ --investigation INV
```

If insufficient, try one or two synonyms. Inspect only promising ids:

```bash
jst actions metadata metric <resolved-id> --project PROJ --investigation INV
```

Stop once a useful candidate set exists. Do not page the full catalog.

### Resolve a company and its peers

```bash
jst actions search entities "FTAI Aviation" --limit 10 \\
  --project PROJ --investigation INV
jst actions metadata entity <company-id> --project PROJ --investigation INV
jst actions relationships <company-id> --relationship classified_as \\
  --project PROJ --investigation INV
jst actions search entities --relation classified_as:<industry-id> \\
  --taxonomy sec-central-index-key --limit 50 \\
  --project PROJ --investigation INV
```

### Exploratory latest values (triage)

```bash
jst actions query run \\
  --metric <metric-id> --entity <e1> --entity <e2> --tail 1 \\
  --project PROJ --investigation INV
# Persist exploratory (in_report=false), then promote later:
jst actions query run --metric <metric-id> --entity <e1> --tail 1 --save \\
  --name "Peer revenue" --project PROJ --investigation INV
jst actions promote <event-id> --project PROJ --investigation INV
# Or promote a saved exploratory query:
jst actions promote --query 1 --project PROJ --investigation INV
```

### Copy research across tabs

```bash
jst actions copy --project PROJ --from fundamentals --to peers \\
  --entity <company-id> --query 1
```

### Rank / membership via search (no invented country ids)

```bash
jst actions search entities "european union" --limit 10 \\
  --project PROJ --investigation INV
jst actions search entities --relation part_of:<eu-entity-id> --taxonomy country \\
  --limit 50 --project PROJ --investigation INV
jst actions search metrics gdp --taxonomy country --limit 20 \\
  --project PROJ --investigation INV
```

### Observability and feedback

```bash
jst actions log --project PROJ --investigation INV --status error
jst actions inspect <event-id>
jst actions feedback submit "Chart clipped on peers tab" \\
  --project PROJ --investigation INV --screen entity
jst actions feedback ls
```

### Local multi-tab preview

```bash
jst preview   # project with in_report queries only; excludes action history
```

Remote multi-tab publish awaits Workstream C; `jst publish` on a multi-tab project
notifies and refuses to silently flatten.

### Prepare a human project

1. Resolve ids via `jst actions search …`.
2. Work against the assigned `--project` / `--investigation` (or ask the human to
   add a tab with `jst new --project <id> --title …`).
3. Promote report queries; leave exploratory artifacts in the log.
4. Hand them: `jst open` / `jst ls` (interactive project picker).

Do not substitute extra research for completing the requested handoff.
"""

_COMPOSITION = """\
## Projects, investigations, and curation

Research lives in a **project** with ordered **investigation** tabs. Each tab has
its own workspace, saved queries, view tree, and contributes events to the shared
action log.

```bash
jst ls                                          # interactive project picker
jst new --project ftai-aviation --title Peers   # add a tab (opens TUI)
jst actions workspace show --project PROJ --investigation INV
jst actions query ls --project PROJ --investigation INV
jst actions promote <event-id> --project PROJ --investigation INV
jst actions promote --query 1 --project PROJ --investigation INV
jst preview
```

- **Humans** use the chart-first shell: sidebar queries/workspace, `/` search,
  `n` new draft, `e` edit, `s` save, `a` annotate, `H` history, `F` feedback,
  `{`/`}` tabs, `g` research root. Enter on a metric/entity opens a draft chart.
  Host `dd` is the delete chord.
- **Agents** use `jst actions …` with explicit `--project` / `--investigation`.
  Do not steal the human viewport.
- **Curation:** exploratory by default; `in_report` only after promote. Copy with
  `jst actions copy` (no API re-fetch). Duplicate a tab with `:tab dup`.
- **Legacy** single-investigation YAML still loads / publishes / clones. Multi-tab
  remote publish is blocked pending Workstream C; use `jst preview` locally.
- Demo: `jst open plain-bagel` still works as a single-investigation project.

Active resume pointer: `~/.jstdata/.active.yaml` (bare `jst` only).
"""

_WORKSPACE_HOWTO = """\
### Workspace contents

Each investigation has a single `workspace:` mapping (numeric `id` default `0`),
optional `label` / `description`, and resource id lists. Query filters
(frequency, head/tail, units, …) belong on `queries:`, not the workspace bag.

Legacy files with `workspaces:` (a list) load id `0` / the first entry and ignore
the rest.

Stage resources into the workspace bag from the TUI (`:search` / space collect).
Agents inspect with `jst actions workspace show` and promote report queries rather
than editing YAML. Prefer resolved slug ids only.
"""

_AUDITED_WORKFLOW = """\
## Audited research workflow

```
resolve ids  →  jst actions …  →  inspect event/artifact  →  promote  →  hand off
```

1. **Resolve** — `jst actions search entities|metrics` (+ relation / taxonomy filters).
2. **Act** — metadata, relationships, taxonomy membership; every call returns
   `event_id` and may mint an immutable artifact.
3. **Inspect** — `jst actions log` / `jst actions inspect <event-id>` reopens the
   historical payload without another Data API request.
4. **Promote** — `jst actions promote` sets `Query.in_report` for local preview /
   legacy publish.
5. **Hand off** — tell the human the project id; they continue in the TUI while
   agents keep targeting other tabs.

Raw `jst api` and the Python client remain for general-purpose data consumption
outside this audited path. Do not treat them as the research workflow.
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

    def walk(
        cmd: click.Command, parts: list[str], ctx: click.Context
    ) -> Iterable[tuple[str, click.Command]]:
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
        "## TUI views (action catalog)",
        "",
        "Reusable analytical UIs inside investigations (distinct from `jst actions`",
        "research ops). Prefer **views** (`table`, `metric`, `entity`, `metrics`) for",
        "read/drill; keep `console` / `discover` / `rank` when the human still needs",
        "to stage or explore. Agents do **not** drive these UIs — hand the human",
        "`jst open` and let them `:add <id>` from the TUI palette.",
        "",
        "| Id | Name | Description | Arguments | Example |",
        "|----|------|-------------|-----------|---------|",
    ]
    if not specs:
        lines.append("| _(none registered)_ | | | | |")
        return "\n".join(lines)

    for spec in specs:
        example = spec.example or f":add {spec.id}"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_md_cell(spec.id)}`",
                    _md_cell(spec.name),
                    _md_cell(spec.description),
                    _md_cell(_format_action_args(spec)),
                    f"`{_md_cell(example)}`",
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("### View details")
    for spec in specs:
        lines.append("")
        lines.append(f"#### `{spec.id}` — {spec.name}")
        lines.append("")
        lines.append(spec.description.strip())
        lines.append("")
        if spec.arguments:
            lines.append("Seed arguments (JSON keys for TUI `:add` / `--args`):")
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
        example = spec.example or f":add {spec.id}"
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
        _AUDITED_WORKFLOW.rstrip(),
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
        "execute. Audited research ops live under `jst actions`; soft-deprecated "
        "low-level resource commands remain under `jst api`.",
        "",
        render_commands_table(cli_group),
        "",
    ]
    return "\n".join(sections)
