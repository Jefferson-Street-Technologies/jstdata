"""Project-local universal-search history (report-scoped)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Optional

from .project_store import project_dir

MAX_ENTRIES = 30


def search_history_path(
    project_id: str, *, app_dir: Optional[Path] = None
) -> Path:
    return project_dir(project_id, app_dir=app_dir) / "search_history.json"


def load_search_history(
    project_id: Optional[str],
    *,
    app_dir: Optional[Path] = None,
) -> list[dict[str, str]]:
    """Return newest-first entries ``{query, resource}`` for the project."""
    if not project_id:
        return []
    path = search_history_path(project_id, app_dir=app_dir)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        resource = str(item.get("resource") or "all").strip() or "all"
        out.append({"query": query, "resource": resource})
    return out[:MAX_ENTRIES]


def remember_search(
    project_id: Optional[str],
    *,
    query: str,
    resource: str = "all",
    app_dir: Optional[Path] = None,
) -> list[dict[str, str]]:
    """Prepend a search; de-dupe exact query+resource. Returns updated list."""
    query = (query or "").strip()
    if not project_id or not query:
        return load_search_history(project_id, app_dir=app_dir)
    resource = (resource or "all").strip() or "all"
    existing = load_search_history(project_id, app_dir=app_dir)
    entry = {"query": query, "resource": resource}
    filtered = [
        e
        for e in existing
        if not (e.get("query") == query and e.get("resource") == resource)
    ]
    updated = [entry, *filtered][:MAX_ENTRIES]
    _save(project_id, updated, app_dir=app_dir)
    return updated


def _save(
    project_id: str,
    entries: list[dict[str, str]],
    *,
    app_dir: Optional[Path] = None,
) -> None:
    root = project_dir(project_id, app_dir=app_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    path = search_history_path(project_id, app_dir=app_dir)
    payload: list[dict[str, Any]] = [
        {"query": e["query"], "resource": e["resource"]} for e in entries
    ]
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=str(root),
        )
        tmp = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
            tmp.replace(path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass
