"""Coached tutorials: catalog, coach logic, and progress state.

A tutorial is one YAML file under ``onboarding/tutorials/``. It embeds a
ready-made investigation (workspace + action tree) and the coached phases
that walk through it. Starting one installs that investigation fresh as
``tutorial-<id>``; the host restores the user's investigation on leave.

Levels describe how much the coach spells out:

- ``guided``   — every key press is named.
- ``assisted`` — goals plus exact keys; you decide what to collect.
- ``open``     — a research question; the coach only checks progress.

The coach is poll-based: the host builds a :class:`CoachSnapshot` of what is
on screen and the coach checks the current phase's ``until`` rules against
it, so action screens need no tutorial hooks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from ..investigation.model import Investigation

LEVELS = ("guided", "assisted", "open")
LEVEL_LABELS = {
    "guided": "BEGINNER · guided",
    "assisted": "INTERMEDIATE · assisted",
    "open": "ADVANCED · open",
}
TUTORIAL_PREFIX = "tutorial-"
STATE_FILENAME = "onboarding.json"

# ``until`` keys, all ANDed. Counts are minimums.
UNTIL_KEYS = (
    "workspace_metric_count",
    "workspace_entity_count",
    "workspace_has_metric",
    "workspace_has_entity",
    "query_count",
    "active_action",
    "focus",
    "screen",
    "home_dest",
    "screen_flag",
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        return [str(value)]
    return [str(v) for v in value]


@dataclass(frozen=True)
class CoachSnapshot:
    """What the coach can see of the running host."""

    metrics: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    query_count: int = 0
    active_action: Optional[int] = None
    # Ids of the focused widget and its ancestors.
    focus_ids: frozenset[str] = frozenset()
    focus_in_input: bool = False
    screen: str = ""
    home_dest: Optional[str] = None
    # Screen-reported state (``coach_flags()``), e.g. {"loaded", "chart"}.
    screen_flags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TutorialPhase:
    text: str
    do: str = ""
    until: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TutorialPhase:
        until = data.get("until") or {}
        if not isinstance(until, Mapping):
            raise ValueError("Tutorial phase 'until' must be a mapping.")
        unknown = set(until) - {*UNTIL_KEYS, "done"}
        if unknown:
            raise ValueError(f"Unknown 'until' keys: {sorted(unknown)}")
        return cls(
            text=str(data.get("text") or "").strip(),
            do=" ".join(str(data.get("do") or "").split()),
            until=dict(until),
        )

    @property
    def is_done(self) -> bool:
        return bool(self.until.get("done"))

    def satisfied(self, snap: CoachSnapshot) -> bool:
        u = self.until
        if self.is_done or not u:
            return False
        if len(snap.metrics) < int(u.get("workspace_metric_count", 0)):
            return False
        if len(snap.entities) < int(u.get("workspace_entity_count", 0)):
            return False
        if any(m not in snap.metrics for m in _as_list(u.get("workspace_has_metric"))):
            return False
        if any(e not in snap.entities for e in _as_list(u.get("workspace_has_entity"))):
            return False
        if snap.query_count < int(u.get("query_count", 0)):
            return False
        if "active_action" in u and snap.active_action != int(u["active_action"]):
            return False
        if "focus" in u and str(u["focus"]) not in snap.focus_ids:
            return False
        if "screen" in u and snap.screen != str(u["screen"]):
            return False
        if "home_dest" in u and snap.home_dest != str(u["home_dest"]):
            return False
        if any(f not in snap.screen_flags for f in _as_list(u.get("screen_flag"))):
            return False
        return True


@dataclass(frozen=True)
class Tutorial:
    """Bundled tutorial: metadata, the investigation it runs in, and phases."""

    id: str
    title: str
    level: str
    summary: str
    investigation: Mapping[str, Any]
    start_action: int
    phases: tuple[TutorialPhase, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Tutorial:
        tid = str(data.get("id") or "")
        level = str(data.get("level") or "")
        if level not in LEVELS:
            raise ValueError(f"Tutorial {tid!r} level must be one of {LEVELS}.")
        phases = tuple(TutorialPhase.from_dict(p) for p in data.get("phases") or [])
        inv = data.get("investigation")
        if not phases or not isinstance(inv, Mapping):
            raise ValueError(f"Tutorial {tid!r} needs an investigation and phases.")
        return cls(
            id=tid,
            title=str(data.get("title") or tid),
            level=level,
            summary=" ".join(str(data.get("summary") or "").split()),
            investigation=inv,
            start_action=int(data.get("start_action", 0)),
            phases=phases,
        )

    @property
    def investigation_id(self) -> str:
        return f"{TUTORIAL_PREFIX}{self.id}"

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS[self.level]

    def build_investigation(self) -> Investigation:
        """A fresh copy of the tutorial investigation (empty workspace)."""
        data = dict(self.investigation)
        data["id"] = self.investigation_id
        data.setdefault("title", f"Tutorial · {self.title}")
        inv = Investigation.from_dict(data)
        inv.validate()
        return inv


def _tutorials_dir():
    return files("jstdata.onboarding").joinpath("tutorials")


def list_tutorials() -> list[Tutorial]:
    """All bundled tutorials, easiest level first."""
    items = []
    for entry in _tutorials_dir().iterdir():
        if entry.name.endswith(".yaml"):
            items.append(
                Tutorial.from_dict(yaml.safe_load(entry.read_text(encoding="utf-8")))
            )
    return sorted(items, key=lambda t: (LEVELS.index(t.level), t.id))


def load_tutorial(tutorial_id: str) -> Tutorial:
    for tutorial in list_tutorials():
        if tutorial.id == tutorial_id:
            return tutorial
    known = ", ".join(t.id for t in list_tutorials())
    raise KeyError(f"No tutorial named {tutorial_id!r}. Available: {known}")


@dataclass
class TutorialCoach:
    tutorial: Tutorial
    phase_index: int = 0

    @property
    def current_phase(self) -> Optional[TutorialPhase]:
        if self.phase_index >= len(self.tutorial.phases):
            return None
        return self.tutorial.phases[self.phase_index]

    @property
    def is_finished(self) -> bool:
        phase = self.current_phase
        return phase is None or phase.is_done

    @property
    def progress_label(self) -> str:
        total = len(self.tutorial.phases)
        return f"Step {min(self.phase_index + 1, total)} / {total}"

    def advance(self, snap: CoachSnapshot) -> bool:
        """Move past every satisfied phase; True if anything changed."""
        moved = False
        while (phase := self.current_phase) is not None and phase.satisfied(snap):
            self.phase_index += 1
            moved = True
        return moved

    def render(self, snap: CoachSnapshot) -> tuple[str, str, str]:
        """Header, explanation, and the exact keys to press right now."""
        header = f"TUTORIAL · {self.tutorial.title} · {self.progress_label}"
        phase = self.current_phase
        if phase is None:
            return header, "Tutorial complete.", "▶ DO: press : → type tutorial → Enter for more"
        u = phase.until
        do = phase.do
        # Navigation keys don't work while typing; say so instead of the plan.
        needs_nav = "active_action" in u or "screen" in u or "home_dest" in u
        if snap.focus_in_input and needs_nav:
            do = "You're in a text box. Press Esc to leave it first."
        if do:
            do = f"▶ DO: {do}"
        progress = []
        if "workspace_entity_count" in u:
            progress.append(f"{len(snap.entities)}/{u['workspace_entity_count']} entities")
        if "workspace_metric_count" in u:
            progress.append(f"{len(snap.metrics)}/{u['workspace_metric_count']} metrics")
        if progress:
            # Plain text: square brackets would be parsed as Textual markup.
            do = f"{do}   · collected so far: {', '.join(progress)}".strip()
        return header, phase.text, do


# --- Progress state (<app_dir>/onboarding.json) ---


def _state_path(app_dir: Path) -> Path:
    return app_dir / STATE_FILENAME


def load_state(app_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_state_path(app_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(app_dir: Path, state: dict[str, Any]) -> None:
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
        _state_path(app_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # progress is a nicety; never break the TUI over it


def should_prompt(app_dir: Path) -> bool:
    """True until the user has seen the first-run tutorial menu once."""
    return not load_state(app_dir).get("prompted")


def mark_prompted(app_dir: Path) -> None:
    state = load_state(app_dir)
    state["prompted"] = True
    _save_state(app_dir, state)


def completed_tutorials(app_dir: Path) -> set[str]:
    return set(_as_list(load_state(app_dir).get("completed")))


def mark_completed(app_dir: Path, tutorial_id: str) -> None:
    state = load_state(app_dir)
    state["completed"] = sorted(completed_tutorials(app_dir) | {tutorial_id})
    _save_state(app_dir, state)
