"""Active investigation context: ``~/.jstdata/.active.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from ..client import APP_DIR
from .model import DEFAULT_WORKSPACE_ID, InvestigationError, ROOT_ACTION_ID

ACTIVE_FILENAME = ".active.yaml"


@dataclass
class ActiveContext:
    """Currently focused investigation / workspace / action."""

    investigation: str
    workspace: int = DEFAULT_WORKSPACE_ID
    action: int = ROOT_ACTION_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "investigation": self.investigation,
            "workspace": self.workspace,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ActiveContext:
        if not isinstance(data, Mapping):
            raise InvestigationError("Active context must be a mapping.")
        inv = data.get("investigation")
        if not inv or not isinstance(inv, str):
            raise InvestigationError(
                "Active context requires a string 'investigation'."
            )
        try:
            workspace = int(data.get("workspace", DEFAULT_WORKSPACE_ID))
        except (TypeError, ValueError) as exc:
            raise InvestigationError(
                "Active context 'workspace' must be an integer."
            ) from exc
        try:
            action = int(data.get("action", ROOT_ACTION_ID))
        except (TypeError, ValueError) as exc:
            raise InvestigationError(
                "Active context 'action' must be an integer."
            ) from exc
        return cls(investigation=inv, workspace=workspace, action=action)


def active_path(*, app_dir: Optional[Path] = None) -> Path:
    base = app_dir if app_dir is not None else APP_DIR
    return base / ACTIVE_FILENAME


def load_active(*, app_dir: Optional[Path] = None) -> ActiveContext:
    """Load the active context file."""
    target = active_path(app_dir=app_dir)
    if not target.is_file():
        raise InvestigationError(f"No active context at {target}.")
    try:
        with open(target, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise InvestigationError(f"Could not parse {target}: {exc}") from exc
    if data is None:
        raise InvestigationError(f"Active context file {target} is empty.")
    return ActiveContext.from_dict(data)


def save_active(
    ctx: ActiveContext, *, app_dir: Optional[Path] = None
) -> Path:
    """Write the active context file (creates the app dir as needed)."""
    target = active_path(app_dir=app_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            ctx.to_dict(),
            f,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    return target


def try_load_active(*, app_dir: Optional[Path] = None) -> Optional[ActiveContext]:
    """Return active context or None if missing/unreadable."""
    try:
        return load_active(app_dir=app_dir)
    except (InvestigationError, OSError):
        return None
