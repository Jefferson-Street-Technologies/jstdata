"""Explicit project / investigation targeting for research ops."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..investigation.active import try_load_active
from ..investigation.model import Investigation, InvestigationError
from ..investigation.project_store import (
    find_investigation_project,
    load_project,
    load_project_investigation,
)
from .result import OperationError


@dataclass(frozen=True)
class ResearchTarget:
    project_id: str
    investigation_id: str
    investigation: Investigation


def resolve_target(
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
    require: bool = True,
) -> Optional[ResearchTarget]:
    """Resolve ``--project`` / ``--investigation``, or the active context.

    Agents should always pass explicit flags. Bare human CLIs may fall back to
    ``.active.yaml``. When ``require`` is False and nothing is targeted,
    returns None (discovery ops that don't need research state).
    """
    if project is None and investigation is None:
        active = try_load_active(app_dir=app_dir)
        if active is None:
            if require:
                raise OperationError(
                    "No project/investigation targeted. Pass --project and "
                    "--investigation, or run `jst open <project>` first."
                )
            return None
        project = active.project
        investigation = active.investigation
        if project is None and investigation:
            project = find_investigation_project(investigation, app_dir=app_dir)

    if investigation is None and project is not None:
        try:
            proj = load_project(project, app_dir=app_dir)
        except InvestigationError as exc:
            raise OperationError(str(exc)) from exc
        investigation = proj.investigations[0]
    if project is None and investigation is not None:
        project = find_investigation_project(investigation, app_dir=app_dir)

    if not project or not investigation:
        if require:
            raise OperationError(
                "Could not resolve project/investigation. Pass --project and "
                "--investigation explicitly."
            )
        return None

    try:
        inv = load_project_investigation(
            project, investigation, app_dir=app_dir
        )
    except InvestigationError as exc:
        raise OperationError(str(exc)) from exc
    return ResearchTarget(
        project_id=project,
        investigation_id=investigation,
        investigation=inv,
    )
