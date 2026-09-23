"""Investigation command-mode grammar (``:`` palette)."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional


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
        usage="search <metric|entity|series> <query>",
    ),
    CommandSpec(
        "add",
        "Add an action under the active (or --parent) node",
        usage="add <type> [--args JSON] [--parent N] [--no-activate]",
    ),
    CommandSpec(
        "rm",
        "Remove an action (cascade); confirm with --force when needed",
        usage="rm <action-id> [--force]",
    ),
)

_COMMAND_BY_NAME = {c.name: c for c in COMMANDS}
SEARCH_TYPES = frozenset({"metric", "entity", "series"})


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


def parse_command_line(raw: str) -> ParsedCommand:
    """Parse a command line without the leading ``:``."""
    text = raw.strip()
    if text.startswith(":"):
        text = text[1:].strip()
    if not text:
        raise CommandError("Empty command. Try: workspace, search, add, rm")
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
    if name == "add":
        return _parse_add(rest)
    if name == "rm":
        return _parse_rm(rest)
    raise CommandError(f"Unhandled command {name!r}")


def _parse_search(rest: list[str]) -> ParsedCommand:
    if len(rest) < 2:
        raise CommandError("Usage: search <metric|entity|series> <query>")
    rtype = rest[0].lower()
    if rtype not in SEARCH_TYPES:
        raise CommandError(
            f"Unknown resource type {rest[0]!r}. "
            "Use metric, entity, or series."
        )
    query = " ".join(rest[1:]).strip()
    if not query:
        raise CommandError("Usage: search <metric|entity|series> <query>")
    return ParsedCommand(name="search", args=[rtype, query])


def _parse_add(rest: list[str]) -> ParsedCommand:
    if not rest:
        raise CommandError(
            "Usage: add <type> [--args JSON] [--parent N] [--no-activate] "
            "[--description TEXT]"
        )
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
        if tok in ("--args", "--parent", "--description"):
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
            else:
                options["description"] = value
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
