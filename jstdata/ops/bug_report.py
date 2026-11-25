"""Local bug reports — JSON files under ``~/.jstdata/bug_reports/``."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..client import APP_DIR


def bug_reports_dir(*, app_dir: Optional[Path] = None) -> Path:
    base = app_dir if app_dir is not None else APP_DIR
    path = base / "bug_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_bug_report_id() -> str:
    return f"bug_{uuid.uuid4().hex[:12]}"


def save_bug_report(
    message: str,
    *,
    replication: dict[str, Any],
    screen: str,
    project_id: Optional[str] = None,
    investigation_id: Optional[str] = None,
    app_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Write one bug report JSON. Returns the saved payload (incl. path)."""
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report_id = new_bug_report_id()
    payload: dict[str, Any] = {
        "bug_report_id": report_id,
        "created_at": created,
        "message": (message or "").strip() or "(no comment)",
        "screen": screen,
        "project_id": project_id,
        "investigation_id": investigation_id,
        "replication": replication,
    }
    out_dir = bug_reports_dir(app_dir=app_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}_{report_id}.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    payload["path"] = str(path)
    return payload
