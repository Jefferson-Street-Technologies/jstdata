"""SQLite action log + immutable artifact files under ``~/.jstdata/``."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .. import client as _client

SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path(*, app_dir: Optional[Path] = None) -> Path:
    base = app_dir if app_dir is not None else _client.APP_DIR
    return base / "state.sqlite"


def default_artifacts_dir(*, app_dir: Optional[Path] = None) -> Path:
    base = app_dir if app_dir is not None else _client.APP_DIR
    return base / "artifacts"


def inputs_hash(operation: str, inputs: dict[str, Any]) -> str:
    payload = json.dumps(
        {"operation": operation, "inputs": inputs},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ActionLogStore:
    """Durable event log and artifact index."""

    def __init__(
        self,
        *,
        app_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> None:
        self.app_dir = app_dir
        self.db_path = Path(db_path) if db_path else default_db_path(app_dir=app_dir)
        self.artifacts_dir = (
            Path(artifacts_dir)
            if artifacts_dir
            else default_artifacts_dir(app_dir=app_dir)
        )
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    inputs_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    investigation_id TEXT,
                    actor TEXT,
                    operation TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    artifact_id TEXT,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER,
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_completed
                    ON events(completed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_project
                    ON events(project_id, completed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_investigation
                    ON events(investigation_id, completed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_artifacts_hash
                    ON artifacts(operation, content_hash);
                """
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    def new_event_id(self) -> str:
        return f"evt_{uuid.uuid4().hex[:12]}"

    def new_artifact_id(self) -> str:
        return f"art_{uuid.uuid4().hex[:12]}"

    def find_cached_artifact(
        self,
        operation: str,
        inputs: dict[str, Any],
        *,
        max_age_seconds: Optional[int] = 12 * 3600,
    ) -> Optional[dict[str, Any]]:
        """Return artifact row + loaded data when an equivalent cache entry exists."""
        digest = inputs_hash(operation, inputs)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT artifact_id, path, created_at, content_hash
                FROM artifacts
                WHERE operation = ? AND content_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (operation, digest),
            ).fetchone()
        if row is None:
            return None
        if max_age_seconds is not None:
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > max_age_seconds:
                return None
        data = self.load_artifact_data(row["artifact_id"], path=row["path"])
        return {
            "artifact_id": row["artifact_id"],
            "path": row["path"],
            "created_at": row["created_at"],
            "content_hash": row["content_hash"],
            "data": data,
        }

    def write_artifact(
        self,
        *,
        operation: str,
        inputs: dict[str, Any],
        data: Any,
        artifact_id: Optional[str] = None,
    ) -> str:
        artifact_id = artifact_id or self.new_artifact_id()
        digest = inputs_hash(operation, inputs)
        rel_path = f"{artifact_id}.json"
        abs_path = self.artifacts_dir / rel_path
        payload = {
            "artifact_id": artifact_id,
            "operation": operation,
            "inputs": inputs,
            "data": data,
            "created_at": _utc_now(),
        }
        tmp = abs_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(abs_path)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, operation, content_hash, path, created_at, inputs_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    operation,
                    digest,
                    rel_path,
                    payload["created_at"],
                    json.dumps(inputs, sort_keys=True, default=str),
                ),
            )
        return artifact_id

    def load_artifact_data(
        self, artifact_id: str, *, path: Optional[str] = None
    ) -> Any:
        if path is None:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT path FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
            if row is None:
                raise KeyError(f"Unknown artifact {artifact_id!r}")
            path = row["path"]
        abs_path = self.artifacts_dir / path
        payload = json.loads(abs_path.read_text(encoding="utf-8"))
        return payload.get("data")

    def record_event(
        self,
        *,
        event_id: str,
        operation: str,
        status: str,
        inputs: dict[str, Any],
        started_at: str,
        completed_at: str,
        duration_ms: int,
        project_id: Optional[str] = None,
        investigation_id: Optional[str] = None,
        actor: Optional[str] = None,
        error: Optional[str] = None,
        artifact_id: Optional[str] = None,
        cache_hit: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events(
                    event_id, project_id, investigation_id, actor, operation,
                    started_at, completed_at, inputs_json, status, error,
                    artifact_id, cache_hit, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    project_id,
                    investigation_id,
                    actor,
                    operation,
                    started_at,
                    completed_at,
                    json.dumps(inputs, sort_keys=True, default=str),
                    status,
                    error,
                    artifact_id,
                    1 if cache_hit else 0,
                    duration_ms,
                ),
            )

    def list_events(
        self,
        *,
        project_id: Optional[str] = None,
        investigation_id: Optional[str] = None,
        operation: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if investigation_id:
            clauses.append("investigation_id = ?")
            params.append(investigation_id)
        if operation:
            clauses.append("operation = ?")
            params.append(operation)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, project_id, investigation_id, actor, operation,
                       started_at, completed_at, inputs_json, status, error,
                       artifact_id, cache_hit, duration_ms
                FROM events
                {where}
                ORDER BY completed_at DESC, event_id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "event_id": row["event_id"],
                    "project_id": row["project_id"],
                    "investigation_id": row["investigation_id"],
                    "actor": row["actor"],
                    "operation": row["operation"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "inputs": json.loads(row["inputs_json"]),
                    "status": row["status"],
                    "error": row["error"],
                    "artifact_id": row["artifact_id"],
                    "cache_hit": bool(row["cache_hit"]),
                    "duration_ms": row["duration_ms"],
                }
            )
        return out

    def get_event(self, event_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT event_id, project_id, investigation_id, actor, operation,
                       started_at, completed_at, inputs_json, status, error,
                       artifact_id, cache_hit, duration_ms
                FROM events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown event {event_id!r}")
        event = {
            "event_id": row["event_id"],
            "project_id": row["project_id"],
            "investigation_id": row["investigation_id"],
            "actor": row["actor"],
            "operation": row["operation"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "inputs": json.loads(row["inputs_json"]),
            "status": row["status"],
            "error": row["error"],
            "artifact_id": row["artifact_id"],
            "cache_hit": bool(row["cache_hit"]),
            "duration_ms": row["duration_ms"],
        }
        if event["artifact_id"]:
            event["artifact"] = {
                "artifact_id": event["artifact_id"],
                "data": self.load_artifact_data(event["artifact_id"]),
            }
        return event
