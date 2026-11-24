"""Structured operation results for the shared actions layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


class OperationError(ValueError):
    """User-facing failure from a research operation."""


@dataclass
class OperationResult:
    """Outcome of one research operation (success or structured failure)."""

    operation: str
    status: str  # success | error | cache_hit
    event_id: str
    data: Any = None
    error: Optional[str] = None
    project_id: Optional[str] = None
    investigation_id: Optional[str] = None
    inputs: dict[str, Any] = field(default_factory=dict)
    artifact_id: Optional[str] = None
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
