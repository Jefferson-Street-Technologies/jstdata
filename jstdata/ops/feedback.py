"""Local feedback outbox (SQLite) until Workstream C ingest exists."""

from __future__ import annotations

import json
import platform
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .logstore import ActionLogStore, _utc_now
from .result import OperationResult
from .runner import get_store

_SECRET_KEYS = re.compile(
    r"(api[_-]?key|authorization|password|secret|token|bearer)",
    re.IGNORECASE,
)


def _redact(value: Any) -> Any:
    """Drop secret-looking keys; leave structure otherwise."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SECRET_KEYS.search(str(k)):
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def ensure_feedback_schema(store: ActionLogStore) -> None:
    with store._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_outbox (
                feedback_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                project_id TEXT,
                investigation_id TEXT,
                screen TEXT,
                diagnostics_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_created
                ON feedback_outbox(created_at DESC)
            """
        )


def new_feedback_id() -> str:
    return f"fb_{uuid.uuid4().hex[:12]}"


def capture_diagnostics(
    *,
    screen: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Bundle safe local diagnostics for a feedback submission."""
    payload: dict[str, Any] = {
        "captured_at": _utc_now(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "screen": screen,
    }
    if extra:
        payload["extra"] = extra
    return _redact(payload)


def submit_feedback(
    message: str,
    *,
    project_id: Optional[str] = None,
    investigation_id: Optional[str] = None,
    screen: Optional[str] = None,
    diagnostics: Optional[dict[str, Any]] = None,
    app_dir: Optional[Path] = None,
    store: Optional[ActionLogStore] = None,
) -> OperationResult:
    """Queue a feedback bundle in the local outbox (no network)."""
    text = (message or "").strip()
    if not text:
        return OperationResult(
            operation="feedback.submit",
            status="error",
            event_id="evt_feedback",
            error="Feedback message cannot be empty.",
            inputs={"message": message},
        )
    log = store or get_store(app_dir=app_dir)
    ensure_feedback_schema(log)
    feedback_id = new_feedback_id()
    diag = diagnostics or capture_diagnostics(screen=screen)
    diag = _redact(diag)
    created = _utc_now()
    with log._connect() as conn:
        conn.execute(
            """
            INSERT INTO feedback_outbox(
                feedback_id, created_at, status, message,
                project_id, investigation_id, screen, diagnostics_json
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                created,
                text,
                project_id,
                investigation_id,
                screen,
                json.dumps(diag, sort_keys=True, default=str),
            ),
        )
    return OperationResult(
        operation="feedback.submit",
        status="success",
        event_id="evt_feedback",
        data={
            "feedback_id": feedback_id,
            "status": "queued",
            "created_at": created,
            "message": text,
            "project_id": project_id,
            "investigation_id": investigation_id,
            "screen": screen,
            "diagnostics": diag,
        },
        inputs={"message": text, "screen": screen},
        project_id=project_id,
        investigation_id=investigation_id,
    )


def list_feedback(
    *,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    app_dir: Optional[Path] = None,
    store: Optional[ActionLogStore] = None,
) -> OperationResult:
    log = store or get_store(app_dir=app_dir)
    ensure_feedback_schema(log)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with log._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT feedback_id, created_at, status, message,
                   project_id, investigation_id, screen, diagnostics_json
            FROM feedback_outbox
            {where}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    events = []
    for row in rows:
        events.append(
            {
                "feedback_id": row["feedback_id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "message": row["message"],
                "project_id": row["project_id"],
                "investigation_id": row["investigation_id"],
                "screen": row["screen"],
                "diagnostics": json.loads(row["diagnostics_json"] or "{}"),
            }
        )
    return OperationResult(
        operation="feedback.list",
        status="success",
        event_id="evt_feedback_list",
        data={"feedback": events, "count": len(events)},
        inputs={"status": status, "limit": limit, "offset": offset},
    )


def inspect_feedback(
    feedback_id: str,
    *,
    app_dir: Optional[Path] = None,
    store: Optional[ActionLogStore] = None,
) -> OperationResult:
    log = store or get_store(app_dir=app_dir)
    ensure_feedback_schema(log)
    with log._connect() as conn:
        row = conn.execute(
            """
            SELECT feedback_id, created_at, status, message,
                   project_id, investigation_id, screen, diagnostics_json
            FROM feedback_outbox
            WHERE feedback_id = ?
            """,
            (feedback_id,),
        ).fetchone()
    if row is None:
        return OperationResult(
            operation="feedback.inspect",
            status="error",
            event_id="evt_feedback_inspect",
            error=f"Unknown feedback {feedback_id!r}",
            inputs={"feedback_id": feedback_id},
        )
    return OperationResult(
        operation="feedback.inspect",
        status="success",
        event_id="evt_feedback_inspect",
        data={
            "feedback_id": row["feedback_id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "message": row["message"],
            "project_id": row["project_id"],
            "investigation_id": row["investigation_id"],
            "screen": row["screen"],
            "diagnostics": json.loads(row["diagnostics_json"] or "{}"),
        },
        inputs={"feedback_id": feedback_id},
        project_id=row["project_id"],
        investigation_id=row["investigation_id"],
    )
