"""Action catalog and shared helpers for investigation screens.

Actions are reusable analytical UIs registered into investigations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

from ..client import JSTDataClient
from ..investigation.model import Workspace

# (client, workspace, **kwargs) -> textual.screen.Screen
CreateScreenFn = Callable[..., Any]


@dataclass(frozen=True)
class ActionArgument:
    """One seed flag for an action screen (does not reproduce the UI)."""

    name: str
    type: str
    description: str
    required: bool = False
    default: Any = None
    choices: Optional[tuple[str, ...]] = None
    multiple: bool = False

    def flag(self) -> str:
        return "--" + self.name.replace("_", "-")

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "flag": self.flag(),
            "type": self.type,
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "multiple": self.multiple,
        }
        if self.choices is not None:
            data["choices"] = list(self.choices)
        return data


@dataclass(frozen=True)
class ActionBinding:
    """One keybinding an action exposes (help + introspection)."""

    key: str
    action: str
    description: str
    show_in_help: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "action": self.action,
            "description": self.description,
            "show_in_help": self.show_in_help,
        }


@dataclass(frozen=True)
class ActionSpec:
    """Self-describing interactive action. Catalog + investigation host plug in."""

    id: str
    name: str
    description: str
    create_screen: CreateScreenFn
    arguments: tuple[ActionArgument, ...] = ()
    bindings: tuple[ActionBinding, ...] = ()
    example: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "arguments": [a.to_dict() for a in self.arguments],
            "bindings": [b.to_dict() for b in self.bindings if b.show_in_help],
            "example": self.example
            or f"jst action add {self.id}",
        }


def resource_type_for_id(workspace: Workspace, resource_id: str) -> str:
    """Return metric|entity|series for an id present in the workspace."""
    if resource_id in workspace.metric:
        return "metric"
    if resource_id in workspace.entity:
        return "entity"
    if resource_id in workspace.series:
        return "series"
    return "series"


def hydrate_labels(
    client: JSTDataClient,
    workspace: Workspace,
    labels: dict[str, str],
    *,
    cache=None,
) -> None:
    """Fill UI labels for workspace resource IDs (in-place).

    Uses the persistent resource label cache: if any staged id is missing or
    older than 12 hours, the whole workspace is refreshed via ``/resource``.
    """
    from ..resource_cache import default_resource_cache

    store = cache if cache is not None else default_resource_cache()
    store.hydrate_workspace(client, workspace, labels)


def label_for(labels: dict[str, str], resource_id: str) -> str:
    """Display name for a resource id; falls back to the id itself."""
    return labels.get(resource_id, resource_id)


_REGISTRY: Dict[str, ActionSpec] = {}


def register(spec: ActionSpec) -> ActionSpec:
    """Register an action (idempotent by id)."""
    _REGISTRY[spec.id] = spec
    return spec


def get_action(action_id: str) -> ActionSpec:
    try:
        return _REGISTRY[action_id]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown action {action_id!r}. Available: {known}") from exc


def list_actions() -> list[ActionSpec]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def _coerce(arg: ActionArgument, raw: str) -> Any:
    if arg.choices is not None and raw not in arg.choices:
        allowed = ", ".join(arg.choices)
        raise ValueError(
            f"Invalid value {raw!r} for {arg.flag()}. Choices: {allowed}"
        )
    if arg.type in ("integer", "int"):
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{arg.flag()} expects an integer, got {raw!r}"
            ) from exc
    if arg.type in ("boolean", "bool"):
        lowered = raw.lower()
        if lowered in ("1", "true", "yes"):
            return True
        if lowered in ("0", "false", "no"):
            return False
        raise ValueError(f"{arg.flag()} expects a boolean, got {raw!r}")
    return raw


def parse_action_kwargs(spec: ActionSpec, tokens: Sequence[str]) -> dict[str, Any]:
    """Parse flag tokens against an action's declared arguments (tests / tooling)."""
    by_flag = {a.flag(): a for a in spec.arguments}
    kwargs: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        flag = tok
        inline: Optional[str] = None
        if tok.startswith("--") and "=" in tok:
            flag, inline = tok.split("=", 1)
        arg = by_flag.get(flag)
        if arg is None:
            raise ValueError(
                f"Unknown option {flag} for action {spec.id!r}. "
                f"See: jst action show {spec.id}"
            )
        if arg.type in ("boolean", "bool") and inline is None:
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                nxt = tokens[i + 1].lower()
                if nxt in ("1", "true", "yes", "0", "false", "no"):
                    kwargs[arg.name] = _coerce(arg, tokens[i + 1])
                    i += 2
                    continue
            kwargs[arg.name] = True
            i += 1
            continue
        if inline is not None:
            value = _coerce(arg, inline)
            i += 1
        else:
            if i + 1 >= len(tokens):
                raise ValueError(f"{flag} requires a value.")
            value = _coerce(arg, tokens[i + 1])
            i += 2
        if arg.multiple:
            kwargs.setdefault(arg.name, []).append(value)
        else:
            kwargs[arg.name] = value

    for arg in spec.arguments:
        if arg.name in kwargs:
            continue
        if arg.required:
            raise ValueError(
                f"Action {spec.id!r} requires {arg.flag()}. "
                f"See: jst action show {spec.id}"
            )
        if arg.default is not None:
            kwargs[arg.name] = arg.default
    return kwargs


def format_action_help(spec: ActionSpec) -> str:
    """Human-readable ``jst action show <id>`` text."""
    lines = [
        spec.name,
        "",
        spec.description,
        "",
        "Options (seed args for `jst action add` --args JSON):",
    ]
    if spec.arguments:
        for arg in spec.arguments:
            req = " (required)" if arg.required else ""
            default = (
                f"  [default: {arg.default}]" if arg.default is not None else ""
            )
            lines.append(f"  {arg.flag()} {arg.type.upper()}{req}{default}")
            lines.append(f"      {arg.description}")
            if arg.multiple:
                lines.append("      Repeatable.")
            if arg.choices:
                lines.append(f"      Choices: {', '.join(arg.choices)}")
            lines.append("")
    else:
        lines.append("  (none)")
        lines.append("")

    lines.append("Keybindings:")
    visible = [b for b in spec.bindings if b.show_in_help]
    if visible:
        for b in visible:
            lines.append(f"  {b.key:<16} {b.description}")
        lines.append("")
    else:
        lines.append("  (none)")
        lines.append("")

    lines.append("Example:")
    lines.append(
        f"  {spec.example or f'jst action add {spec.id}'}"
    )
    return "\n".join(lines).rstrip() + "\n"
