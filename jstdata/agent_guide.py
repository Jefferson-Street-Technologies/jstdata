"""Generate the markdown agent bootstrap guide (`jst agent-guide`).

CLI commands, interactive steps, and session field names are derived from the
live Click tree, step registry, and ``Session`` dataclass. Conceptual glue
(policy, modes, recipes, data model, sessions) is static prose that changes rarely.
"""

from __future__ import annotations

import re
from dataclasses import fields
from importlib.metadata import PackageNotFoundError, version
from typing import Iterable, get_args, get_origin

import click

from .session import Session
from .workflows import list_steps

_PACKAGE = "jstdata"

# Session fields that expand the action space without helping typical agent work.
_SESSION_FIELDS_OMIT = frozenset(
    {"start_date", "end_date", "start_time", "end_time"}
)

_HARD_RULES = """\
## Hard rules

- **Never invent IDs.** Never invent metric, entity, taxonomy, or series ids from
  English names. Resolve via search (or scoped list) first.
- **Resolve before use.** Resolve ids before `jst query`, session JSON, or workflow args.
- **Use `--format json`** on resource and search commands unless the user needs a table.
- **Do not launch interactive TUIs** (`jst run`, `jst workflow run`) unless the user
  explicitly asks you to operate the UI. Prepare a Session/workflow and give them the
  launch command.
- **Do not paginate exhaustively.** During discovery, use `--limit` 20–50. Do not walk
  `offset` through the catalog unless the returned set is clearly insufficient.
- **Do not enumerate** `jst metric ls`, `jst series ls`, or full taxonomy populations
  unless the task requires enumeration. Prefer `search` with a query (and taxonomy /
  relation filters when relevant).
- **Do not retrieve deep history** until the candidate universe is narrowed. Use
  `tail=1` (or similarly shallow queries) for triage.
- **Do not create a workflow** when direct CLI analysis satisfies the request. Use
  workflows for human handoff of an interactive investigation.
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
6. **Hand off TUIs to the human.** Prepare Session JSON and/or a saved workflow; print
   the `jst workflow run … --session …` (or `jst run …`) command for them.
7. **Prefer JSON** for anything you will parse or reason over.
"""

_OPERATING_MODES = """\
## Choose an operating mode

### Catalog discovery

Example: “Find metrics relevant to defense spending.”

Use search / show. Do **not** retrieve observations unless needed to disambiguate
candidates. Select a useful set and stop.

### Agent analysis

Example: “Compare military expenditure as a share of GDP across NATO countries.”

Resolve ids → bounded `jst query` → deeper `series observations` only if needed →
answer from structured CLI output. No TUI or workflow required.

### Human handoff

Example: “Set me up to explore defense spending across Europe.”

Resolve resources → write Session JSON → compose/save a workflow when useful → hand
the user the launch command. Do not substitute extra research for completing the
handoff.
"""

_DATA_MODEL = """\
## Data model

- **Metric** — a measurable theme (e.g. GDP, employed-persons). Identified by a slug id.
- **Entity** — a context the metric is measured in (e.g. a country, a company). Slug id.
- **Taxonomy** — a named population of entities (e.g. `country`, `us-county`,
  `sec-central-index-key`). Use it to scope search, query, and ranking to that population.
- **Series** — one concrete time series: a metric observed for an entity (at a frequency).
- **Session** — portable analytical intent (resource ids + query filters). See **Sessions**
  below. No observations live in a session file.

Ids are opaque slugs. Display names are not ids.
"""

_RESOLUTION = """\
## Resolution

Never invent metric, entity, taxonomy, or series ids from English names.

Always resolve via search (prefer a query string) before writing ids into a workflow,
`--session` JSON, or `jst query` flags. Scope with `--taxonomy` / `--relation` when the
user names a population or relationship. Confirm ambiguous hits with `show` before use.
"""

_RECIPES = """\
## Common recipes

### Find relevant metrics

```bash
jst metric search "defense spending" --taxonomy country --limit 20 --format json
```

If insufficient, try one or two synonyms the same way. Inspect only promising ids:

```bash
jst metric show <resolved-id> --format json
```

Stop once a useful candidate set exists. Do not page the full catalog.

### Compare latest values

```bash
jst query \\
  --metric <metric-id> \\
  --taxonomy country \\
  --tail 1 \\
  --sort-by value \\
  --limit 50 \\
  --format json
```

### Retrieve history after narrowing

```bash
jst metric series <metric-id> --format json
jst series observations <series-id> \\
  --start-date 2015-01-01 \\
  --format json
```

### Rank GDP among EU countries

1. Resolve the EU entity, then list members via relation (do not invent country ids):

```bash
jst entity search "european union" --limit 10 --format json
jst entity search --relation part_of:<eu-entity-id> --taxonomy country \\
  --limit 50 --format json
```

2. Resolve a GDP metric for the country population:

```bash
jst metric search gdp --taxonomy country --limit 20 --format json
jst metric show <gdp-metric-id> --format json
```

3. Write a session with the chosen metric and every EU member entity id:

```json
{
  "metric": ["<gdp-metric-id>"],
  "entity": ["<fr>", "<de>", "<it>", "..."]
}
```

4. Rank **only those session entities** (``rank`` does this by default when the
   session has entities; agents can call ``/query`` the same way):

```bash
jst query \\
  --metric <gdp-metric-id> \\
  --entity <fr> --entity <de> --entity <it> \\
  --tail 1 \\
  --sort-by value \\
  --format json
```

Human handoff:

```bash
jst run --session eu-gdp.json rank
```

With an empty entity list, ``rank`` falls back to the taxonomy/all population.
Pass ``--session-entities=false`` to force that even when entities are staged.
Optional ``--taxonomy country`` still ANDs with session entities when both apply.

### Prepare a human investigation

1. Resolve metric/entity/taxonomy ids via search.
2. Write a Session JSON with those ids and filters (`taxonomy`, `tail`, …).
3. Create or reuse a workflow if the human needs a TUI pipeline.
4. Hand them a launch command, for example:

```bash
jst workflow run eu-analysis --session defense.json
```

Do not substitute extra research for completing the requested handoff.
"""

_COMPOSITION = """\
## Composition and workflows

Interactive investigation is composed in the shell, not a custom DSL:

```bash
jst run STEP [ARGS...] : STEP [ARGS...] : ...
```

- **Steps** are TUI screens for the **human**. They edit one shared session for the run.
- **Step args** seed that step; they are not the session.
- **Saved workflows** (`jst workflow create` / `run`) persist step topology + step args
  only. Bake discovered metrics/entities into a **session JSON** and pass `--session`
  when running (see **Sessions**).
- Pipeline tokens for `workflow create` must follow a `--` boundary, e.g.
  `jst workflow create --id gdp-rank -- console : rank --taxonomy country`.

`jst run` and `jst workflow run` launch interactive TUIs. Agents should normally prepare
the Session/workflow and provide the launch command rather than controlling the TUI.
For step-specific help, run `jst step <id>` (or `jst step <id> --json`) on demand.
"""

_SESSION_HOWTO = """\
### Create and modify

**Write a JSON file** (usual agent path after resolving ids):

```json
{
  "metric": ["employed-persons", "unemployment-rate"],
  "taxonomy": "country",
  "tail": 1,
  "sort_by": "value"
}
```

Omit unused fields. Empty lists may be omitted. Ids must already be resolved.

**Python API** (same shape as the JSON file):

```python
from jstdata import Session

session = Session(
    metric=["employed-persons", "unemployment-rate"],
    taxonomy="country",
    tail=1,
    sort_by="value",
)
session.add_metric("gdp")          # no-op if already present
session.add_entity("united-states")
session.remove_id("gdp")           # removes from metric/entity/series
session.save("labor.json")         # write JSON
session = Session.load("labor.json")
```

Edit the JSON by hand or re-save from Python; there is no separate session CLI.

### Use with steps and workflows

Preload into a pipeline or saved workflow:

```bash
jst run --session labor.json rank --taxonomy country
jst workflow run gdp-rank --session labor.json
```

`--session` copies the file into the live session at startup. The human can still
change it in the TUI. Typical agent pattern after metric discovery:

1. Resolve metric (and taxonomy) ids via search.
2. Write a session JSON with those `metric` ids (and optional filters).
3. Create or reuse a workflow whose steps consume session metrics (e.g. `rank`).
4. Hand the user: `jst workflow run <id> --session <file>.json`.
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


def _format_step_args(spec) -> str:
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
    """Human-readable type for a Session field annotation."""
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


_SESSION_FIELD_NOTES: dict[str, str] = {
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


def render_session_section() -> str:
    """Sessions how-to plus a field table derived from ``Session``."""
    lines = [
        "## Sessions",
        "",
        "A **session** is the portable bag of resource ids and filters that steps share.",
        "It mirrors `jst query` / `JSTDataClient.query` intent: no observations, only what",
        "is needed to (re)run a query or drive a TUI pipeline.",
        "",
        "Saved workflows do **not** store session contents. After discovering metrics or",
        "entities, write them into a session JSON and pass `--session` on `jst run` or",
        "`jst workflow run`. `discover` defaults to session metrics when any are staged",
        "(pass `--session-metrics=false` for the full entity-anchored candidate search);",
        "`rank` defaults to session entities when any are staged.",
        "",
        "### Session JSON fields",
        "",
        "Primary fields for agent-written sessions (from the installed `Session` model).",
        "Prefer `head` / `tail` / `as_of` for query windows. Omit unused fields.",
        "",
        "| Field | Type | Notes |",
        "|-------|------|-------|",
    ]
    for f in fields(Session):
        if f.name in _SESSION_FIELDS_OMIT:
            continue
        note = _SESSION_FIELD_NOTES.get(f.name, "")
        lines.append(
            f"| `{_md_cell(f.name)}` | {_md_cell(_annotation_label(f.type))} | {_md_cell(note)} |"
        )
    lines.append("")
    lines.append(_SESSION_HOWTO.rstrip())
    return "\n".join(lines)


def render_steps_section() -> str:
    specs = list_steps()
    lines = [
        "## Interactive steps",
        "",
        "Atoms for `jst run` / saved workflows (human-facing TUIs). Order-agnostic: any",
        "step accepts an empty or arbitrary session. Do not operate these UIs yourself;",
        "use `jst step <id>` if you need details while helping a human.",
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
                    _md_cell(_format_step_args(spec)),
                    f"`{_md_cell(spec.example or f'jst run {spec.id}')}`",
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("### Step details")
    for spec in specs:
        lines.append("")
        lines.append(f"#### `{spec.id}` — {spec.name}")
        lines.append("")
        lines.append(spec.description.strip())
        lines.append("")
        if spec.arguments:
            lines.append("Arguments:")
            lines.append("")
            for arg in spec.arguments:
                req = "required" if arg.required else "optional"
                default = (
                    f", default `{arg.default}`" if arg.default is not None else ""
                )
                multi = ", repeatable" if arg.multiple else ""
                lines.append(
                    f"- `{arg.flag()}` ({arg.type}, {req}{multi}{default}): "
                    f"{arg.description}"
                )
                if arg.choices:
                    lines.append(
                        f"  - Choices: {', '.join(f'`{c}`' for c in arg.choices)}"
                    )
            lines.append("")
        example = spec.example or f"jst run {spec.id}"
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
        render_session_section(),
        "",
        render_steps_section(),
        "",
        "## CLI commands",
        "",
        "One row per leaf command, derived from the installed CLI. Prefer the recipes "
        "and hard rules above; use this table to confirm flags, not as a checklist to "
        "execute.",
        "",
        render_commands_table(cli_group),
        "",
    ]
    return "\n".join(sections)
