"""Static HTML investigation preview (`jst preview`)."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from ..client import APP_DIR
from .data import (
    PreviewModel,
    build_preview_model,
    build_project_preview_model,
    most_recent_investigation_id,
    resolve_investigation_id,
)
from .progress import ProgressSpinner
from .render import render_html, write_preview_html

if TYPE_CHECKING:
    from ..client import JSTDataClient

ProgressFn = Callable[[str], None]


def default_export_dir() -> Path:
    return APP_DIR / "exports" / "preview"


__all__ = [
    "PreviewModel",
    "ProgressSpinner",
    "build_preview_model",
    "build_project_preview_model",
    "default_export_dir",
    "most_recent_investigation_id",
    "preview_investigation",
    "preview_path",
    "render_html",
    "resolve_investigation_id",
    "write_preview_html",
]


def preview_path(investigation_id: str, *, export_dir: Optional[Path] = None) -> Path:
    base = export_dir if export_dir is not None else default_export_dir()
    return base / f"{investigation_id}.html"


def preview_investigation(
    client: JSTDataClient,
    investigation_id: Optional[str] = None,
    *,
    root: Optional[Path] = None,
    export_dir: Optional[Path] = None,
    open_browser: bool = True,
    progress: Optional[ProgressFn] = None,
) -> Path:
    """Build the preview HTML for an investigation (or multi-tab project)."""
    model: PreviewModel
    if investigation_id is not None and root is None:
        from ..investigation.project_store import project_exists

        if project_exists(investigation_id):
            from ..investigation.project_store import load_project

            project = load_project(investigation_id)
            if len(project.investigations) > 1:
                model = build_project_preview_model(
                    client, investigation_id, progress=progress
                )
            else:
                model = build_preview_model(
                    client,
                    project.investigations[0],
                    root=root,
                    progress=progress,
                )
        else:
            model = build_preview_model(
                client, investigation_id, root=root, progress=progress
            )
    else:
        model = build_preview_model(
            client, investigation_id, root=root, progress=progress
        )
    if progress is not None:
        n_charts = sum(len(p.charts) for p in model.panels)
        progress(
            f"Rendering HTML ({len(model.panels)} quer"
            f"{'y' if len(model.panels) == 1 else 'ies'}, "
            f"{n_charts} chart{'s' if n_charts != 1 else ''})…"
        )
    path = preview_path(model.investigation_id, export_dir=export_dir)
    write_preview_html(model, path)
    if open_browser:
        if progress is not None:
            progress("Opening browser…")
        webbrowser.open(path.resolve().as_uri())
    return path
