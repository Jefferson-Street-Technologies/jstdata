"""Project container: ordered investigations under one research objective."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .model import InvestigationError

PROJECT_FORMAT_VERSION = 1
DEFAULT_INVESTIGATION_ID = "main"


def utc_now_iso() -> str:
    """UTC timestamp suitable for ``created_at`` / ``updated_at``."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_project_timestamp(value: Optional[str]) -> Optional[float]:
    """Parse an ISO timestamp to epoch seconds, or None if missing/invalid."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass
class Project:
    """Directory-backed research objective holding ordered investigations.

    Investigation YAML content is unchanged; the project owns ordering and
    metadata. ``investigations`` lists investigation ids in tab order.
    ``created_at`` / ``updated_at`` are ISO-8601 UTC strings used to sort
    ``jst ls`` (most recently updated first).
    """

    id: str
    title: str
    notes: Optional[str] = None
    investigations: list[str] = field(default_factory=list)
    version: int = PROJECT_FORMAT_VERSION
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def validate(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise InvestigationError("Project id must be a non-empty string.")
        if not self.title or not str(self.title).strip():
            raise InvestigationError("Project title cannot be empty.")
        if not self.investigations:
            raise InvestigationError(
                f"Project {self.id!r} must list at least one investigation."
            )
        seen: set[str] = set()
        for inv_id in self.investigations:
            if not isinstance(inv_id, str) or not inv_id:
                raise InvestigationError(
                    f"Project {self.id!r} has an invalid investigation id."
                )
            if inv_id in seen:
                raise InvestigationError(
                    f"Project {self.id!r} lists investigation {inv_id!r} twice."
                )
            seen.add(inv_id)

    def touch(self, *, created: bool = False) -> None:
        """Bump ``updated_at`` (and ``created_at`` when ``created`` or missing)."""
        now = utc_now_iso()
        if created or not self.created_at:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "id": self.id,
            "title": self.title,
            "investigations": list(self.investigations),
        }
        if self.notes is not None:
            data["notes"] = self.notes
        if self.created_at is not None:
            data["created_at"] = self.created_at
        if self.updated_at is not None:
            data["updated_at"] = self.updated_at
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Project:
        if not isinstance(data, Mapping):
            raise InvestigationError("Project must be a mapping.")
        pid = data.get("id")
        if not pid or not isinstance(pid, str):
            raise InvestigationError("Project requires a string 'id'.")
        title = data.get("title")
        if not title or not isinstance(title, str) or not title.strip():
            raise InvestigationError("Project requires a non-empty 'title'.")
        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise InvestigationError("Project 'notes' must be a string when set.")
        raw_invs = data.get("investigations")
        if not isinstance(raw_invs, list) or not raw_invs:
            raise InvestigationError(
                "Project requires a non-empty 'investigations' list."
            )
        investigations = [str(x) for x in raw_invs]
        version = int(data.get("version", PROJECT_FORMAT_VERSION))
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        if created_at is not None and not isinstance(created_at, str):
            raise InvestigationError("Project 'created_at' must be a string when set.")
        if updated_at is not None and not isinstance(updated_at, str):
            raise InvestigationError("Project 'updated_at' must be a string when set.")
        project = cls(
            id=pid,
            title=title.strip(),
            notes=notes.strip() if isinstance(notes, str) and notes.strip() else None,
            investigations=investigations,
            version=version,
            created_at=created_at,
            updated_at=updated_at,
        )
        project.validate()
        return project

    @classmethod
    def new(
        cls,
        project_id: str,
        *,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        investigation_ids: Optional[list[str]] = None,
    ) -> Project:
        ids = list(investigation_ids) if investigation_ids else [DEFAULT_INVESTIGATION_ID]
        project = cls(
            id=project_id,
            title=(title or project_id).strip(),
            notes=notes,
            investigations=ids,
        )
        project.touch(created=True)
        return project
