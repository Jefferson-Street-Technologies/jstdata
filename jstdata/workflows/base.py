"""Step catalog, pipeline parsing, and shared session helpers.

The shell is the composition language::

    jst run STEP [ARGS...] : STEP [ARGS...] : ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Sequence

from ..client import JSTDataClient
from ..session import Session

# (client, session, **kwargs) -> textual.screen.Screen
CreateScreenFn = Callable[..., Any]


@dataclass(frozen=True)
class StepArgument:
    """One CLI flag that seeds a step's TUI (does not reproduce the UI)."""

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
class StepBinding:
    """One keybinding a step exposes to the host (help + introspection)."""

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
class StepSpec:
    """Self-describing interactive step. Catalog + host plug into this."""

    id: str
    name: str
    description: str
    create_screen: CreateScreenFn
    arguments: tuple[StepArgument, ...] = ()
    bindings: tuple[StepBinding, ...] = ()
    example: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "arguments": [a.to_dict() for a in self.arguments],
            "bindings": [b.to_dict() for b in self.bindings if b.show_in_help],
            "example": self.example or f"jst run {self.id}",
        }


@dataclass(frozen=True)
class ResolvedStep:
    """A catalog step plus parsed kwargs from the shell pipeline."""

    spec: StepSpec
    kwargs: dict[str, Any]


def default_session_path(prefix: str = "session") -> str:
    """Unique-by-default session filename to avoid write conflicts."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = prefix.replace(":", "-") or "session"
    return f"{safe}-{stamp}.json"


def apply_loaded_session(target: Session, loaded: Session) -> None:
    """Copy resource selectors and filters from ``loaded`` into ``target``."""
    target.metric = list(loaded.metric)
    target.entity = list(loaded.entity)
    target.series = list(loaded.series)
    target.frequency = loaded.frequency
    target.taxonomy = loaded.taxonomy
    target.head = loaded.head
    target.tail = loaded.tail
    target.as_of = loaded.as_of
    target.sort_by = loaded.sort_by
    target.start_date = loaded.start_date
    target.end_date = loaded.end_date
    target.start_time = loaded.start_time
    target.end_time = loaded.end_time
    target.order_by = loaded.order_by


def load_session_or_empty(path: Optional[str]) -> Session:
    """Load a session from disk, or return an empty session."""
    if not path:
        return Session()
    return Session.load(path)


def resource_type_for_id(session: Session, resource_id: str) -> str:
    """Return metric|entity|series for an id present in the session."""
    if resource_id in session.metric:
        return "metric"
    if resource_id in session.entity:
        return "entity"
    if resource_id in session.series:
        return "series"
    return "series"


def hydrate_labels(
    client: JSTDataClient,
    session: Session,
    labels: dict[str, str],
) -> None:
    """Fill missing UI labels for session resource IDs (in-place)."""
    missing = [rid for rid in session.resource_ids() if rid not in labels]
    if not missing:
        return
    for resource in client.get_resources(missing):
        labels[resource.id] = resource.label or resource.id


def label_for(labels: dict[str, str], resource_id: str) -> str:
    """Display name for a resource id; falls back to the id itself."""
    return labels.get(resource_id, resource_id)


_REGISTRY: Dict[str, StepSpec] = {}


def register(spec: StepSpec) -> StepSpec:
    """Register a step (idempotent by id)."""
    _REGISTRY[spec.id] = spec
    return spec


def get_step(step_id: str) -> StepSpec:
    try:
        return _REGISTRY[step_id]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown step {step_id!r}. Available: {known}") from exc


def list_steps() -> list[StepSpec]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


class PipelineError(ValueError):
    """Invalid ``jst run`` pipeline."""


def split_pipeline(tokens: Sequence[str]) -> list[list[str]]:
    """Split argv on standalone ``:`` into per-step token groups."""
    if not tokens:
        raise PipelineError("No steps given. Example: jst run console")

    chunks: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok == ":":
            if not current:
                raise PipelineError("Empty step in pipeline (stray ':').")
            chunks.append(current)
            current = []
        else:
            current.append(tok)
    if not current:
        raise PipelineError("Trailing ':' with no step.")
    chunks.append(current)
    return chunks


def _coerce(arg: StepArgument, raw: str) -> Any:
    if arg.choices is not None and raw not in arg.choices:
        allowed = ", ".join(arg.choices)
        raise PipelineError(
            f"Invalid value {raw!r} for {arg.flag()}. Choices: {allowed}"
        )
    if arg.type in ("integer", "int"):
        try:
            return int(raw)
        except ValueError as exc:
            raise PipelineError(f"{arg.flag()} expects an integer, got {raw!r}") from exc
    if arg.type in ("boolean", "bool"):
        lowered = raw.lower()
        if lowered in ("1", "true", "yes"):
            return True
        if lowered in ("0", "false", "no"):
            return False
        raise PipelineError(f"{arg.flag()} expects a boolean, got {raw!r}")
    return raw


def parse_step_kwargs(spec: StepSpec, tokens: Sequence[str]) -> dict[str, Any]:
    """Parse a step's flag tokens against its declared arguments."""
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
            raise PipelineError(
                f"Unknown option {flag} for step {spec.id!r}. See: jst step {spec.id}"
            )
        if arg.type in ("boolean", "bool") and inline is None:
            # Bare ``--flag`` => True. Optional next token may be true/false.
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
                raise PipelineError(f"{flag} requires a value.")
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
            raise PipelineError(
                f"Step {spec.id!r} requires {arg.flag()}. See: jst step {spec.id}"
            )
        if arg.default is not None:
            kwargs[arg.name] = arg.default
    return kwargs


def resolve_pipeline(tokens: Sequence[str]) -> list[ResolvedStep]:
    """Turn raw ``jst run`` tokens into catalog steps + kwargs."""
    resolved: list[ResolvedStep] = []
    for chunk in split_pipeline(tokens):
        step_id, *rest = chunk
        spec = get_step(step_id)
        kwargs = parse_step_kwargs(spec, rest)
        resolved.append(ResolvedStep(spec=spec, kwargs=kwargs))
    return resolved


def format_step_help(spec: StepSpec) -> str:
    """Human-readable ``jst step <id>`` text."""
    lines = [
        spec.name,
        "",
        spec.description,
        "",
        "Options:",
    ]
    if spec.arguments:
        for arg in spec.arguments:
            req = " (required)" if arg.required else ""
            default = f"  [default: {arg.default}]" if arg.default is not None else ""
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
    lines.append(f"  {spec.example or f'jst run {spec.id}'}")
    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(
    client: JSTDataClient,
    tokens: Sequence[str],
    *,
    session_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> None:
    """Launch the host over a resolved step pipeline."""
    run_resolved_pipeline(
        client,
        resolve_pipeline(tokens),
        session_path=session_path,
        output_path=output_path,
    )


def run_resolved_pipeline(
    client: JSTDataClient,
    steps: list[ResolvedStep],
    *,
    session_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> None:
    """Launch the host over an already-resolved step list (no re-parse)."""
    from .host import WorkflowHost

    app = WorkflowHost(
        client,
        steps,
        session_path=session_path,
        output_path=output_path,
    )
    app.run()
