"""Bootstrap the bundled tutorial investigation and default active context."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from importlib.resources import files

from .active import ActiveContext, save_active, try_load_active
from .model import DEFAULT_WORKSPACE_ID, Investigation, ROOT_ACTION_ID
from .store import (
    TUTORIAL_ID,
    ensure_investigations_dir,
    investigation_exists,
    load_investigation,
    save_investigation,
)

BUNDLED_TUTORIAL = "tutorial.yaml"
BUNDLED_PLAIN_BAGEL = "plain-bagel.yaml"
PLAIN_BAGEL_ID = "plain-bagel"


def _investigations_root(app_dir: Optional[Path]) -> Optional[Path]:
    if app_dir is None:
        return None
    return app_dir / "investigations"


def bundled_tutorial_text() -> str:
    return (
        files("jstdata.investigation.bundled")
        .joinpath(BUNDLED_TUTORIAL)
        .read_text(encoding="utf-8")
    )


def load_bundled_tutorial() -> Investigation:
    data = yaml.safe_load(bundled_tutorial_text())
    return Investigation.from_dict(data)


def bundled_plain_bagel_text() -> str:
    return (
        files("jstdata.investigation.bundled")
        .joinpath(BUNDLED_PLAIN_BAGEL)
        .read_text(encoding="utf-8")
    )


def load_bundled_plain_bagel() -> Investigation:
    data = yaml.safe_load(bundled_plain_bagel_text())
    return Investigation.from_dict(data)


def ensure_plain_bagel(
    *,
    app_dir: Optional[Path] = None,
    force: bool = False,
) -> Investigation:
    """Install the Plain Bagel demo investigation if missing (or force-refresh)."""
    inv_root = _investigations_root(app_dir)
    ensure_investigations_dir(root=inv_root)
    if force or not investigation_exists(PLAIN_BAGEL_ID, root=inv_root):
        inv = load_bundled_plain_bagel()
        save_investigation(inv, root=inv_root)
        return inv
    return load_investigation(PLAIN_BAGEL_ID, root=inv_root)


def ensure_bootstrap(
    *,
    app_dir: Optional[Path] = None,
    force_tutorial: bool = False,
) -> tuple[Investigation, ActiveContext]:
    """Ensure tutorial investigation + ``.active.yaml`` exist.

    On first launch (tutorial missing), writes both files. If ``.active.yaml``
    is missing but the tutorial exists, recreates active pointing at tutorial.

    ``force_tutorial`` rewrites the tutorial from the bundled template (tests).
    Also ensures the Plain Bagel provenance demo investigation is present.
    """
    inv_root = _investigations_root(app_dir)
    if app_dir is not None:
        app_dir.mkdir(parents=True, exist_ok=True)
    ensure_investigations_dir(root=inv_root)

    if force_tutorial or not investigation_exists(TUTORIAL_ID, root=inv_root):
        tutorial = load_bundled_tutorial()
        save_investigation(tutorial, root=inv_root)
    else:
        tutorial = load_investigation(TUTORIAL_ID, root=inv_root)

    # Demo for View/Context + release provenance — install if absent.
    ensure_plain_bagel(app_dir=app_dir, force=False)

    ctx = try_load_active(app_dir=app_dir)
    if ctx is None:
        ctx = ActiveContext(
            investigation=TUTORIAL_ID,
            workspace=DEFAULT_WORKSPACE_ID,
            action=ROOT_ACTION_ID,
        )
        save_active(ctx, app_dir=app_dir)

    return tutorial, ctx
