"""Persist projects under ``~/.jstdata/projects/<id>/``."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import yaml

from .. import client as _client
from .batch_writer import ProjectBatchWriter
from .model import Investigation, InvestigationError
from .project import DEFAULT_INVESTIGATION_ID, Project, parse_project_timestamp
from .store import (
    allocate_investigation_id,
    investigation_path,
    list_investigations as list_legacy_investigations,
    load_investigation,
    validate_investigation_id,
)
from . import store as _store

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LEGACY_MIGRATED_MARKER = ".migrated-to-projects"


def validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not _SLUG_RE.fullmatch(project_id):
        raise InvestigationError(
            f"Invalid project id {project_id!r}. "
            "Use a slug like 'ftai-aviation' "
            "(lowercase letters, digits, hyphens)."
        )
    return project_id


def slugify_title(title: str) -> str:
    """Best-effort slug from a display title (may need uniqueness checks)."""
    raw = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return raw or "project"


def projects_dir(*, app_dir: Optional[Path] = None) -> Path:
    # Read APP_DIR dynamically so tests can monkeypatch ``client.APP_DIR``.
    return (
        _client.APP_DIR / "projects"
        if app_dir is None
        else app_dir / "projects"
    )


def legacy_investigations_dir(*, app_dir: Optional[Path] = None) -> Path:
    return (
        _store.INVESTIGATIONS_DIR
        if app_dir is None
        else app_dir / "investigations"
    )


def project_dir(project_id: str, *, app_dir: Optional[Path] = None) -> Path:
    validate_project_id(project_id)
    return projects_dir(app_dir=app_dir) / project_id


def project_yaml_path(project_id: str, *, app_dir: Optional[Path] = None) -> Path:
    return project_dir(project_id, app_dir=app_dir) / "project.yaml"


def project_investigations_dir(
    project_id: str, *, app_dir: Optional[Path] = None
) -> Path:
    return project_dir(project_id, app_dir=app_dir) / "investigations"


def project_exists(project_id: str, *, app_dir: Optional[Path] = None) -> bool:
    return project_yaml_path(project_id, app_dir=app_dir).is_file()


def allocate_project_id(
    *,
    preferred: Optional[str] = None,
    app_dir: Optional[Path] = None,
) -> str:
    """Allocate an unused project slug (``proj-<unix>`` or ``preferred``)."""
    if preferred:
        base = validate_project_id(preferred)
    else:
        base = f"proj-{int(time.time())}"
    candidate = base
    n = 2
    while project_exists(candidate, app_dir=app_dir):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def ensure_projects_dir(*, app_dir: Optional[Path] = None) -> Path:
    base = projects_dir(app_dir=app_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_project(project: Project, *, app_dir: Optional[Path] = None) -> Path:
    """Write ``project.yaml`` only (investigations unchanged)."""
    project.validate()
    validate_project_id(project.id)
    project.touch()
    target_dir = project_dir(project.id, app_dir=app_dir)
    with ProjectBatchWriter(target_dir) as writer:
        writer.stage_project(project)
    return project_yaml_path(project.id, app_dir=app_dir)


def load_project(project_id: str, *, app_dir: Optional[Path] = None) -> Project:
    validate_project_id(project_id)
    target = project_yaml_path(project_id, app_dir=app_dir)
    if not target.is_file():
        raise InvestigationError(f"Project {project_id!r} not found.")
    try:
        with open(target, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise InvestigationError(f"Could not parse {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvestigationError(f"Project file {target} is not a mapping.")
    project = Project.from_dict(data)
    if project.id != project_id:
        raise InvestigationError(
            f"Project id mismatch: directory {project_id!r} "
            f"declares id {project.id!r}."
        )
    return project


def _project_activity_mtime(
    project_id: str, *, app_dir: Optional[Path] = None
) -> float:
    """Newest mtime among project.yaml and investigation YAMLs (filesystem fallback)."""
    base = project_dir(project_id, app_dir=app_dir)
    newest = 0.0
    yaml_path = base / "project.yaml"
    if yaml_path.is_file():
        try:
            newest = max(newest, yaml_path.stat().st_mtime)
        except OSError:
            pass
    inv_dir = base / "investigations"
    if inv_dir.is_dir():
        for path in inv_dir.glob("*.yaml"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def project_sort_key(
    project: Project, *, app_dir: Optional[Path] = None
) -> tuple[float, float, str]:
    """Sort key: updated_at, then created_at, then filesystem mtime; descending via negation."""
    updated = parse_project_timestamp(project.updated_at)
    created = parse_project_timestamp(project.created_at)
    mtime = _project_activity_mtime(project.id, app_dir=app_dir)
    # Prefer explicit updated_at; else created_at; else mtime.
    primary = updated if updated is not None else (
        created if created is not None else mtime
    )
    secondary = created if created is not None else mtime
    return (-primary, -secondary, project.id)


def list_projects(*, app_dir: Optional[Path] = None) -> list[str]:
    """Return project ids, most recently updated first.

    Uses ``updated_at`` when present, else ``created_at``, else the newest
    mtime among ``project.yaml`` and investigation files.
    """
    base = projects_dir(app_dir=app_dir)
    if not base.exists():
        return []
    ranked: list[tuple[tuple[float, float, str], str]] = []
    for path in base.iterdir():
        if not (path.is_dir() and (path / "project.yaml").is_file()):
            continue
        pid = path.name
        try:
            project = load_project(pid, app_dir=app_dir)
            key = project_sort_key(project, app_dir=app_dir)
        except InvestigationError:
            mtime = _project_activity_mtime(pid, app_dir=app_dir)
            key = (-mtime, -mtime, pid)
        ranked.append((key, pid))
    ranked.sort(key=lambda item: item[0])
    return [pid for _, pid in ranked]


def save_project_investigation(
    project_id: str,
    inv: Investigation,
    *,
    app_dir: Optional[Path] = None,
    project: Optional[Project] = None,
) -> Path:
    """Persist an investigation under a project; refresh project timestamps."""
    validate_project_id(project_id)
    validate_investigation_id(inv.id)
    target_dir = project_dir(project_id, app_dir=app_dir)
    if project is None:
        try:
            project = load_project(project_id, app_dir=app_dir)
        except InvestigationError:
            project = Project.new(
                project_id,
                title=inv.title or project_id,
                notes=inv.notes,
                investigation_ids=[inv.id],
            )
    if inv.id not in project.investigations:
        project.investigations.append(inv.id)
    project.touch()
    with ProjectBatchWriter(target_dir) as writer:
        writer.stage_project(project)
        writer.stage_investigation(inv)
    return project_investigations_dir(project_id, app_dir=app_dir) / f"{inv.id}.yaml"


def load_project_investigation(
    project_id: str,
    investigation_id: str,
    *,
    app_dir: Optional[Path] = None,
) -> Investigation:
    root = project_investigations_dir(project_id, app_dir=app_dir)
    return load_investigation(investigation_id, root=root)


def find_investigation_project(
    investigation_id: str, *, app_dir: Optional[Path] = None
) -> Optional[str]:
    """Return the project id that owns ``investigation_id``, if any."""
    validate_investigation_id(investigation_id)
    for pid in list_projects(app_dir=app_dir):
        inv_path = (
            project_investigations_dir(pid, app_dir=app_dir)
            / f"{investigation_id}.yaml"
        )
        if inv_path.is_file():
            return pid
    return None


def list_all_investigations(*, app_dir: Optional[Path] = None) -> list[tuple[str, str]]:
    """Return ``(project_id, investigation_id)`` pairs in project then inv order."""
    pairs: list[tuple[str, str]] = []
    for pid in list_projects(app_dir=app_dir):
        try:
            project = load_project(pid, app_dir=app_dir)
        except InvestigationError:
            continue
        for inv_id in project.investigations:
            pairs.append((pid, inv_id))
    return pairs


def create_project_with_investigation(
    *,
    project_id: Optional[str] = None,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    investigation_id: Optional[str] = None,
    investigation_title: Optional[str] = None,
    app_dir: Optional[Path] = None,
) -> tuple[Project, Investigation]:
    """Create a project directory containing one investigation."""
    ensure_projects_dir(app_dir=app_dir)
    if project_id is None:
        preferred = slugify_title(title) if title else None
        project_id = allocate_project_id(preferred=preferred, app_dir=app_dir)
    else:
        validate_project_id(project_id)
        if project_exists(project_id, app_dir=app_dir):
            raise InvestigationError(f"Project {project_id!r} already exists.")

    if investigation_id is None:
        investigation_id = DEFAULT_INVESTIGATION_ID
    else:
        validate_investigation_id(investigation_id)

    project_title = (title or project_id).strip()
    inv_title = (investigation_title or project_title).strip()
    project = Project.new(
        project_id,
        title=project_title,
        notes=notes,
        investigation_ids=[investigation_id],
    )
    inv = Investigation.new(investigation_id, title=inv_title, notes=notes)
    save_project_investigation(
        project_id, inv, app_dir=app_dir, project=project
    )
    return project, inv


def migrate_legacy_investigations(*, app_dir: Optional[Path] = None) -> list[str]:
    """Move flat ``investigations/*.yaml`` into one-investigation projects.

    Idempotent. Returns project ids created or updated by this pass.
    Already-project-backed ids are skipped. Files that collide with an
    existing project investigation are left in place with a warning path
    suffix only if move fails — prefer skip when project already owns the id.
    """
    legacy_root = legacy_investigations_dir(app_dir=app_dir)
    if not legacy_root.exists():
        return []

    marker = legacy_root / _LEGACY_MIGRATED_MARKER
    migrated: list[str] = []
    ensure_projects_dir(app_dir=app_dir)

    for inv_id in list_legacy_investigations(root=legacy_root):
        src = investigation_path(inv_id, root=legacy_root)
        if not src.is_file():
            continue

        existing = find_investigation_project(inv_id, app_dir=app_dir)
        if existing is not None:
            # Already present under a project — drop the legacy copy.
            src.unlink(missing_ok=True)
            continue

        # Prefer project id == investigation id for 1:1 legacy mapping.
        if project_exists(inv_id, app_dir=app_dir):
            # Project exists but doesn't contain this inv — allocate a suffix.
            pid = allocate_project_id(preferred=inv_id, app_dir=app_dir)
        else:
            pid = inv_id

        try:
            inv = load_investigation(inv_id, root=legacy_root)
        except InvestigationError:
            continue

        project = Project.new(
            pid,
            title=inv.title or inv_id,
            notes=inv.notes,
            investigation_ids=[inv.id],
        )
        dest_dir = project_investigations_dir(pid, app_dir=app_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{inv.id}.yaml"
        with ProjectBatchWriter(project_dir(pid, app_dir=app_dir)) as writer:
            writer.stage_project(project)
            writer.stage_investigation(inv)
        if dest.is_file():
            src.unlink(missing_ok=True)
            migrated.append(pid)

    # Marker helps operators see migration ran; safe to rewrite.
    try:
        legacy_root.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"migrated_at: {time.time()}\nmigrated_projects: {migrated}\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return migrated


def add_investigation_to_project(
    project_id: str,
    *,
    investigation_id: Optional[str] = None,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    app_dir: Optional[Path] = None,
) -> Investigation:
    """Append a new investigation tab to an existing project."""
    project = load_project(project_id, app_dir=app_dir)
    root = project_investigations_dir(project_id, app_dir=app_dir)
    if investigation_id is None:
        investigation_id = allocate_investigation_id(root=root)
    else:
        validate_investigation_id(investigation_id)
        if (root / f"{investigation_id}.yaml").is_file():
            raise InvestigationError(
                f"Investigation {investigation_id!r} already exists "
                f"in project {project_id!r}."
            )
        if investigation_id in project.investigations:
            raise InvestigationError(
                f"Investigation {investigation_id!r} is already listed "
                f"on project {project_id!r}."
            )

    inv = Investigation.new(
        investigation_id,
        title=(title or investigation_id).strip(),
        notes=notes,
    )
    save_project_investigation(
        project_id, inv, app_dir=app_dir, project=project
    )
    return inv


def remove_legacy_dir_if_empty(*, app_dir: Optional[Path] = None) -> None:
    """Best-effort cleanup after migration (keeps marker files)."""
    legacy_root = legacy_investigations_dir(app_dir=app_dir)
    if not legacy_root.exists():
        return
    remaining = [p for p in legacy_root.glob("*.yaml") if p.is_file()]
    if remaining:
        return
    # Leave the directory + marker; do not delete user's folder aggressively.
