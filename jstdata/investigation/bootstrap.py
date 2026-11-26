"""Bootstrap bundled investigations into the projects layout."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from importlib.resources import files

from .active import ActiveContext, try_load_active
from .model import Investigation
from .project import Project
from .project_store import (
    find_investigation_project,
    migrate_legacy_investigations,
    project_exists,
    save_project_investigation,
)
from .store import TUTORIAL_ID

BUNDLED_TUTORIAL = "tutorial.yaml"
BUNDLED_PLAIN_BAGEL = "plain-bagel.yaml"
PLAIN_BAGEL_ID = "plain-bagel"


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


def _install_one_inv_project(
    inv: Investigation,
    *,
    app_dir: Optional[Path] = None,
    force: bool = False,
) -> Investigation:
    """Install ``inv`` as a one-investigation project (id == inv.id)."""
    pid = inv.id
    already = find_investigation_project(inv.id, app_dir=app_dir)
    if already is not None and not force:
        from .project_store import load_project_investigation

        return load_project_investigation(already, inv.id, app_dir=app_dir)

    if project_exists(pid, app_dir=app_dir) and already is None and not force:
        # Rare: project dir exists without this investigation — still write.
        pass

    project = Project.new(
        pid,
        title=inv.title or pid,
        notes=inv.notes,
        investigation_ids=[inv.id],
    )
    save_project_investigation(pid, inv, app_dir=app_dir, project=project)
    return inv


def ensure_plain_bagel(
    *,
    app_dir: Optional[Path] = None,
    force: bool = False,
) -> Investigation:
    """Install the Plain Bagel demo as a one-investigation project."""
    if force or find_investigation_project(PLAIN_BAGEL_ID, app_dir=app_dir) is None:
        inv = load_bundled_plain_bagel()
        return _install_one_inv_project(inv, app_dir=app_dir, force=force)
    from .project_store import load_project_investigation

    pid = find_investigation_project(PLAIN_BAGEL_ID, app_dir=app_dir)
    assert pid is not None
    return load_project_investigation(pid, PLAIN_BAGEL_ID, app_dir=app_dir)


def ensure_bootstrap(
    *,
    app_dir: Optional[Path] = None,
    force_tutorial: bool = False,
) -> tuple[Investigation, Optional[ActiveContext]]:
    """Migrate legacy YAMLs, install bundled projects; leave ``.active`` alone.

    Writes ``tutorial`` and ``plain-bagel`` projects when missing.
    ``force_tutorial`` rewrites the tutorial from the bundle.
    """
    if app_dir is not None:
        app_dir.mkdir(parents=True, exist_ok=True)

    migrate_legacy_investigations(app_dir=app_dir)

    if force_tutorial or find_investigation_project(TUTORIAL_ID, app_dir=app_dir) is None:
        tutorial = load_bundled_tutorial()
        _install_one_inv_project(tutorial, app_dir=app_dir, force=force_tutorial)
    else:
        from .project_store import load_project_investigation

        pid = find_investigation_project(TUTORIAL_ID, app_dir=app_dir)
        assert pid is not None
        tutorial = load_project_investigation(pid, TUTORIAL_ID, app_dir=app_dir)

    ensure_plain_bagel(app_dir=app_dir, force=False)
    return tutorial, try_load_active(app_dir=app_dir)
