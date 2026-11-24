"""Project container: ordered investigations under one research objective."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .model import InvestigationError

PROJECT_FORMAT_VERSION = 1
DEFAULT_INVESTIGATION_ID = "main"


@dataclass
class Project:
    """Directory-backed research objective holding ordered investigations.

    Investigation YAML content is unchanged; the project owns ordering and
    metadata. ``investigations`` lists investigation ids in tab order.
    """

    id: str
    title: str
    notes: Optional[str] = None
    investigations: list[str] = field(default_factory=list)
    version: int = PROJECT_FORMAT_VERSION

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

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "id": self.id,
            "title": self.title,
            "investigations": list(self.investigations),
        }
        if self.notes is not None:
            data["notes"] = self.notes
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
        project = cls(
            id=pid,
            title=title.strip(),
            notes=notes.strip() if isinstance(notes, str) and notes.strip() else None,
            investigations=investigations,
            version=version,
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
        return cls(
            id=project_id,
            title=(title or project_id).strip(),
            notes=notes,
            investigations=ids,
        )
