"""Investigation command-mode grammar (``:`` palette)."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from ..add_options import (
    ADD_FLAG_DOCS,
    ADD_FLAGS,
    ADD_USAGE,
    ADD_VALUE_FLAGS,
)


class CommandError(ValueError):
    """User-facing command parse / validation error."""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    usage: str = ""

    def help_line(self) -> str:
        usage = self.usage or self.name
        return f"{usage:<40} {self.summary}"


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "workspace",
        "Open the workspace drawer (staged resources)",
        usage="workspace",
    ),
    CommandSpec(
        "search",
        "Search the catalog and add to the workspace",
        usage="search <metric|entity|series|taxonomy|all> <query>",
    ),
    CommandSpec(
        "query",
        "Save the workspace as a query, or open a saved query",
        usage="query [id]",
    ),
    CommandSpec(
        "rename",
        "Set the investigation title and description",
        usage="rename [title] [--description text]",
    ),
    CommandSpec(
        "add",
        "Add an action under the active (or --parent) node",
        usage=ADD_USAGE,
    ),
    CommandSpec(
        "rm",
        "Remove an action (cascade); confirm with --force when needed",
        usage="rm <action-id> [--force]",
    ),
)

_COMMAND_BY_NAME = {c.name: c for c in COMMANDS}
SEARCH_TYPES = frozenset({"metric", "entity", "series", "taxonomy", "all"})

_RM_FLAGS = ("--force",)

_RM_FLAG_DOCS = {
    "--force": "Confirm cascade delete / removing the active action",
}
_RENAME_FLAGS = ("--description",)
_RENAME_FLAG_DOCS = {
    "--description": "Investigation description (notes)",
}

_SEARCH_TYPE_DOCS = {
    "metric": "Search metrics in the catalog",
    "entity": "Search entities in the catalog",
    "series": "Search series in the catalog",
    "taxonomy": "List taxonomies (/taxonomy), filter locally by query",
    "all": "Unified search (/search) across metrics, entities, and series",
}


@dataclass
class ParsedCommand:
    name: str
    args: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


def list_command_suggestions(prefix: str = "") -> list[CommandSpec]:
    """Filter catalog by name prefix (empty → all)."""
    p = prefix.strip().lower()
    if not p:
        return list(COMMANDS)
    return [c for c in COMMANDS if c.name.startswith(p)]


def completion_doc(candidate: str) -> str:
    """One-line docstring for a Tab-completion candidate."""
    tokens = candidate.strip().split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        spec = _COMMAND_BY_NAME.get(tokens[0].lower())
        return spec.summary if spec else ""

    cmd = tokens[0].lower()
    if cmd == "search" and len(tokens) == 2:
        return _SEARCH_TYPE_DOCS.get(tokens[1].lower(), "")

    if cmd == "add":
        if len(tokens) == 2 and not tokens[1].startswith("-"):
            return _action_description(tokens[1])
        flag = tokens[-1]
        if flag.startswith("-"):
            return ADD_FLAG_DOCS.get(flag, "")

    if cmd == "rm":
        flag = tokens[-1]
        if flag.startswith("-"):
            return _RM_FLAG_DOCS.get(flag, "")

    if cmd == "rename":
        flag = tokens[-1]
        if flag.startswith("-"):
            return _RENAME_FLAG_DOCS.get(flag, "")

    return ""


def action_arg_doc_lines(action_type: str) -> list[str]:
    """Indented lines documenting ``--args`` fields for an action type."""
    import jstdata.actions  # noqa: F401
    import jstdata.investigation.home  # noqa: F401

    from ..actions.base import get_action

    try:
        spec = get_action(action_type)
    except KeyError:
        return []
    if not spec.arguments:
        return ["  (no seed args)"]
    lines: list[str] = []
    for arg in spec.arguments:
        bits = [arg.type]
        if arg.multiple:
            bits.append("repeatable")
        if arg.required:
            bits.append("required")
        if arg.choices:
            bits.append("choices: " + "|".join(arg.choices))
        lines.append(f"  {arg.name} ({', '.join(bits)})  {arg.description}")
    return lines


def _action_description(action_type: str) -> str:
    import jstdata.actions  # noqa: F401
    import jstdata.investigation.home  # noqa: F401

    from ..actions.base import get_action

    try:
        return get_action(action_type).description
    except KeyError:
        return ""


def _action_type_ids() -> list[str]:
    """Registered action type ids (catalog), sorted."""
    import jstdata.actions  # noqa: F401
    import jstdata.investigation.home  # noqa: F401

    from ..actions.base import list_actions

    return [spec.id for spec in list_actions()]


def _strip_leading_colon(raw: str) -> str:
    """Drop a leading ``:`` and its surrounding left-padding; keep a trailing space."""
    i = 0
    while i < len(raw) and raw[i].isspace():
        i += 1
    if i < len(raw) and raw[i] == ":":
        i += 1
        while i < len(raw) and raw[i].isspace():
            i += 1
    return raw[i:]


def _tokens_and_partial(text: str) -> tuple[list[str], str]:
    """Split into completed tokens and the partial token under the cursor.

    A trailing space means the partial token is empty (ready for the next word).
    """
    if not text:
        return [], ""
    ends_space = text[-1].isspace()
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if ends_space:
        return tokens, ""
    if not tokens:
        return [], ""
    return tokens[:-1], tokens[-1]


def _prefix_matches(candidates: list[str], partial: str) -> list[str]:
    p = partial.lower()
    return [c for c in candidates if c.lower().startswith(p)]


def _rebuild(head: list[str], choice: str) -> str:
    return " ".join([*head, choice]).strip()


def complete_command_line(raw: str) -> list[str]:
    """Return candidate command lines for Tab cycling (no leading ``:``).

    Candidates are full lines to substitute into the input. Empty list means
    nothing to complete for the current position.

    When the input is exactly a known command name (e.g. ``add``), candidates
    are the *next* token's options (``add console``, …) so Tab cycles those
    rather than no-oping on the command name itself.
    """
    text = _strip_leading_colon(raw)
    head, partial = _tokens_and_partial(text)

    if not head:
        matches = _prefix_matches([c.name for c in COMMANDS], partial)
        # Exact command name → jump to next-token completions.
        if len(matches) == 1 and matches[0] == partial.lower() and partial:
            return complete_command_line(matches[0] + " ")
        return matches

    cmd = head[0].lower()
    if cmd not in _COMMAND_BY_NAME:
        return []

    if cmd == "workspace":
        return []

    if cmd == "query":
        return []

    if cmd == "rename":
        if "--description" in head:
            return []
        if len(head) == 1:
            matches = _prefix_matches(list(_RENAME_FLAGS), partial)
            return [_rebuild(head, m) for m in matches]
        if partial.startswith("-"):
            matches = _prefix_matches(list(_RENAME_FLAGS), partial)
            return [_rebuild(head, m) for m in matches]
        return []

    if cmd == "search":
        if len(head) == 1:
            matches = _prefix_matches(sorted(SEARCH_TYPES), partial)
            return [_rebuild(head, m) for m in matches]
        return []

    if cmd == "add":
        if len(head) == 1:
            matches = _prefix_matches(_action_type_ids(), partial)
            return [_rebuild(head, m) for m in matches]
        used = set(head[2:])
        if head[-1] in ADD_VALUE_FLAGS:
            return []
        available = [f for f in ADD_FLAGS if f not in used]
        if "--activate" in used:
            available = [f for f in available if f != "--no-activate"]
        if "--no-activate" in used:
            available = [f for f in available if f != "--activate"]
        matches = _prefix_matches(available, partial)
        return [_rebuild(head, m) for m in matches]

    if cmd == "rm":
        if len(head) == 1:
            if partial.startswith("-"):
                matches = _prefix_matches(list(_RM_FLAGS), partial)
                return [_rebuild(head, m) for m in matches]
            return []
        used = set(head[2:])
        available = [f for f in _RM_FLAGS if f not in used]
        matches = _prefix_matches(available, partial)
        return [_rebuild(head, m) for m in matches]

    return []


def parse_command_line(raw: str) -> ParsedCommand:
    """Parse a command line without the leading ``:``."""
    text = raw.strip()
    if text.startswith(":"):
        text = text[1:].strip()
    if not text:
        raise CommandError(
            "Empty command. Try: workspace, search, query, rename, add, rm"
        )
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise CommandError(f"Could not parse command: {exc}") from exc
    if not tokens:
        raise CommandError("Empty command.")
    name = tokens[0].lower()
    if name not in _COMMAND_BY_NAME:
        known = ", ".join(c.name for c in COMMANDS)
        raise CommandError(f"Unknown command {name!r}. Available: {known}")
    rest = tokens[1:]
    if name == "workspace":
        if rest:
            raise CommandError("Usage: workspace")
        return ParsedCommand(name=name)
    if name == "search":
        return _parse_search(rest)
    if name == "query":
        return _parse_query(rest)
    if name == "rename":
        return _parse_rename(rest)
    if name == "add":
        return _parse_add(rest)
    if name == "rm":
        return _parse_rm(rest)
    raise CommandError(f"Unhandled command {name!r}")


def _parse_search(rest: list[str]) -> ParsedCommand:
    if len(rest) < 2:
        raise CommandError(
            "Usage: search <metric|entity|series|taxonomy|all> <query>"
        )
    rtype = rest[0].lower()
    if rtype not in SEARCH_TYPES:
        raise CommandError(
            f"Unknown resource type {rest[0]!r}. "
            "Use metric, entity, series, taxonomy, or all."
        )
    query = " ".join(rest[1:]).strip()
    if not query:
        raise CommandError(
            "Usage: search <metric|entity|series|taxonomy|all> <query>"
        )
    return ParsedCommand(name="search", args=[rtype, query])


def _parse_rename(rest: list[str]) -> ParsedCommand:
    title_parts: list[str] = []
    description: Optional[str] = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--description", "--notes"):
            description = " ".join(rest[i + 1 :])
            break
        if tok.startswith("-"):
            raise CommandError(
                f"Unexpected token {tok!r}. Usage: rename [title] [--description text]"
            )
        title_parts.append(tok)
        i += 1
    title = " ".join(title_parts).strip() or None
    return ParsedCommand(
        name="rename",
        args=list(title_parts),
        options={"title": title, "description": description},
    )


def _parse_query(rest: list[str]) -> ParsedCommand:
    if not rest:
        return ParsedCommand(name="query")
    if len(rest) > 1:
        raise CommandError("Usage: query [id]")
    try:
        query_id = int(rest[0])
    except ValueError as exc:
        raise CommandError("Query id must be an integer") from exc
    return ParsedCommand(name="query", args=[str(query_id)], options={"query_id": query_id})


def _parse_add(rest: list[str]) -> ParsedCommand:
    if not rest:
        raise CommandError(f"Usage: {ADD_USAGE}")
    action_type = rest[0]
    options: dict[str, Any] = {"activate": True}
    i = 1
    while i < len(rest):
        tok = rest[i]
        if tok == "--no-activate":
            options["activate"] = False
            i += 1
            continue
        if tok == "--activate":
            options["activate"] = True
            i += 1
            continue
        if tok == "--force":
            raise CommandError("--force is not valid for add")
        if tok in ADD_VALUE_FLAGS:
            if i + 1 >= len(rest):
                raise CommandError(f"{tok} requires a value")
            value = rest[i + 1]
            if tok == "--args":
                try:
                    data = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise CommandError(f"Invalid --args JSON: {exc}") from exc
                if not isinstance(data, dict):
                    raise CommandError("--args must be a JSON object")
                options["args"] = data
            elif tok == "--parent":
                try:
                    options["parent"] = int(value)
                except ValueError as exc:
                    raise CommandError("--parent must be an integer") from exc
            i += 2
            continue
        raise CommandError(f"Unexpected token {tok!r} in add")
    return ParsedCommand(name="add", args=[action_type], options=options)


def _parse_rm(rest: list[str]) -> ParsedCommand:
    if not rest:
        raise CommandError("Usage: rm <action-id> [--force]")
    try:
        action_id = int(rest[0])
    except ValueError as exc:
        raise CommandError("Action id must be an integer") from exc
    force = False
    for tok in rest[1:]:
        if tok == "--force":
            force = True
        else:
            raise CommandError(f"Unexpected token {tok!r} in rm")
    return ParsedCommand(
        name="rm", args=[str(action_id)], options={"force": force, "action_id": action_id}
    )
