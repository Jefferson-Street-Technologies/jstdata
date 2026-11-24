"""Execute a research operation and (later) append to the action log."""

from __future__ import annotations

import itertools
import time
from typing import Any, Callable, Optional

from .result import OperationError, OperationResult
from .targeting import ResearchTarget

_event_counter = itertools.count(1)


def _next_event_id() -> str:
    return f"evt_{next(_event_counter):04d}_{int(time.time())}"


def run_operation(
    operation: str,
    fn: Callable[[], Any],
    *,
    inputs: Optional[dict[str, Any]] = None,
    target: Optional[ResearchTarget] = None,
) -> OperationResult:
    """Run ``fn``, wrap the outcome, and mint a placeholder event id.

    Slice 3 persists these to SQLite. Callers still get a stable event_id today
    so CLI/TUI can print one.
    """
    event_id = _next_event_id()
    try:
        data = fn()
    except OperationError as exc:
        return OperationResult(
            operation=operation,
            status="error",
            event_id=event_id,
            error=str(exc),
            inputs=dict(inputs or {}),
            project_id=target.project_id if target else None,
            investigation_id=target.investigation_id if target else None,
        )
    except Exception as exc:  # noqa: BLE001 — surface as structured error
        return OperationResult(
            operation=operation,
            status="error",
            event_id=event_id,
            error=str(exc),
            inputs=dict(inputs or {}),
            project_id=target.project_id if target else None,
            investigation_id=target.investigation_id if target else None,
        )
    return OperationResult(
        operation=operation,
        status="success",
        event_id=event_id,
        data=data,
        inputs=dict(inputs or {}),
        project_id=target.project_id if target else None,
        investigation_id=target.investigation_id if target else None,
    )
