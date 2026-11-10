"""Persist investigations as YAML under ``~/.jstdata/investigations/``."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import yaml

from ..client import APP_DIR
from .model import Investigation, InvestigationError

INVESTIGATIONS_DIR = APP_DIR / "investigations"
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TUTORIAL_ID = "tutorial"


def validate_investigation_id(investigation_id: str) -> str:
    """Require a lowercase slug: ``housing-slowdown``, not ``Housing``."""
    if not _SLUG_RE.match(investigation_id):
        raise InvestigationError(
            f"Invalid investigation id {investigation_id!r}. "
            "Use a slug like 'housing-slowdown' "
            "(lowercase letters, digits, hyphens)."
        )
    return investigation_id


def allocate_investigation_id(*, root: Optional[Path] = None) -> str:
    """Generate an unused slug (``inv-<unix>``, with a suffix on collision)."""
    base = f"inv-{int(time.time())}"
    candidate = base
    n = 2
    while investigation_exists(candidate, root=root):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def investigations_dir(*, root: Optional[Path] = None) -> Path:
    return root if root is not None else INVESTIGATIONS_DIR


def investigation_path(
    investigation_id: str, *, root: Optional[Path] = None
) -> Path:
    validate_investigation_id(investigation_id)
    return investigations_dir(root=root) / f"{investigation_id}.yaml"


def investigation_exists(
    investigation_id: str, *, root: Optional[Path] = None
) -> bool:
    return investigation_path(investigation_id, root=root).is_file()


def ensure_investigations_dir(*, root: Optional[Path] = None) -> Path:
    base = investigations_dir(root=root)
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_investigation(
    inv: Investigation, *, root: Optional[Path] = None
) -> Path:
    """Write an investigation YAML file."""
    inv.validate()
    validate_investigation_id(inv.id)
    ensure_investigations_dir(root=root)
    target = investigation_path(inv.id, root=root)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            inv.to_dict(),
            f,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    return target


def load_investigation(
    investigation_id: str, *, root: Optional[Path] = None
) -> Investigation:
    """Load and validate an investigation from disk."""
    validate_investigation_id(investigation_id)
    target = investigation_path(investigation_id, root=root)
    if not target.is_file():
        raise InvestigationError(f"Investigation {investigation_id!r} not found.")
    try:
        with open(target, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise InvestigationError(f"Could not parse {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise InvestigationError(f"Investigation file {target} is not a mapping.")
    inv = Investigation.from_dict(data)
    if inv.id != investigation_id:
        raise InvestigationError(
            f"Investigation id mismatch: file {investigation_id!r} "
            f"declares id {inv.id!r}."
        )
    return inv


def list_investigations(*, root: Optional[Path] = None) -> list[str]:
    """Return investigation ids found on disk (sorted)."""
    base = investigations_dir(root=root)
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.yaml"))
