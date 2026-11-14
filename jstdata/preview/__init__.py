"""Static HTML investigation preview (`jst preview`)."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..client import APP_DIR
from .data import (
    PreviewModel,
    build_preview_model,
    most_recent_investigation_id,
    resolve_investigation_id,
)
from .render import render_html, write_preview_html

if TYPE_CHECKING:
    from ..client import JSTDataClient

def default_export_dir() -> Path:
    return APP_DIR / "exports" / "preview"


__all__ = [
    "PreviewModel",
    "build_preview_model",
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
) -> Path:
    """Build the preview HTML for an investigation and optionally open it."""
    model = build_preview_model(client, investigation_id, root=root)
    path = preview_path(model.investigation_id, export_dir=export_dir)
    write_preview_html(model, path)
    if open_browser:
        webbrowser.open(path.resolve().as_uri())
    return path
