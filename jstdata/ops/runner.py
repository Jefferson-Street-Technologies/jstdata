"""Execute a research operation and append it to the action log."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .logstore import ActionLogStore
from .result import OperationError, OperationResult
from .targeting import ResearchTarget

# Operations whose successful payloads are cached as immutable artifacts.
_CACHEABLE_OPS = {
    "search.entities",
    "search.metrics",
    "taxonomy.list",
    "taxonomy.entities",
    "entity.relationships",
    "metadata.entity",
    "metadata.metric",
    "metadata.series",
    "metadata.release",
    "query.run",
    "query.inspect",
    "coverage.preview",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_store(*, app_dir: Optional[Path] = None) -> ActionLogStore:
    return ActionLogStore(app_dir=app_dir)


def run_operation(
    operation: str,
    fn: Callable[[], Any],
    *,
    inputs: Optional[dict[str, Any]] = None,
    target: Optional[ResearchTarget] = None,
    app_dir: Optional[Path] = None,
    actor: Optional[str] = None,
    use_cache: bool = True,
    store: Optional[ActionLogStore] = None,
) -> OperationResult:
    """Run ``fn``, persist an event (+ artifact when applicable), return the result.

    Cache hits still mint a new event that references the prior immutable
    artifact. Failed ops are logged without overwriting prior artifacts.
    """
    log = store or get_store(app_dir=app_dir)
    event_id = log.new_event_id()
    inputs_dict = dict(inputs or {})
    started = time.monotonic()
    started_at = _utc_now()

    project_id = target.project_id if target else None
    investigation_id = target.investigation_id if target else None

    cache_hit = False
    artifact_id: Optional[str] = None
    data: Any = None
    error: Optional[str] = None
    status = "success"

    if use_cache and operation in _CACHEABLE_OPS:
        cached = log.find_cached_artifact(operation, inputs_dict)
        if cached is not None:
            data = cached["data"]
            artifact_id = cached["artifact_id"]
            cache_hit = True
            status = "cache_hit"

    if not cache_hit:
        try:
            data = fn()
        except OperationError as exc:
            status = "error"
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — surface as structured error
            status = "error"
            error = str(exc)
        else:
            if operation in _CACHEABLE_OPS:
                artifact_id = log.write_artifact(
                    operation=operation,
                    inputs=inputs_dict,
                    data=data,
                )

    completed_at = _utc_now()
    duration_ms = int((time.monotonic() - started) * 1000)
    log.record_event(
        event_id=event_id,
        operation=operation,
        status="success" if status == "cache_hit" else status,
        inputs=inputs_dict,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        project_id=project_id,
        investigation_id=investigation_id,
        actor=actor,
        error=error,
        artifact_id=artifact_id,
        cache_hit=cache_hit,
    )

    return OperationResult(
        operation=operation,
        status=status if status != "cache_hit" else "success",
        event_id=event_id,
        data=data,
        error=error,
        inputs=inputs_dict,
        project_id=project_id,
        investigation_id=investigation_id,
        artifact_id=artifact_id,
        cache_hit=cache_hit,
    )
