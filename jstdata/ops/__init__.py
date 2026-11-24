"""Shared research operations layer (TUI + ``jst actions``).

Every supported research op eventually flows through ``run_operation``.
Action-log persistence lands in a later slice; today the runner returns a
structured result with a placeholder event id.
"""

from .logstore import ActionLogStore
from .result import OperationError, OperationResult
from .runner import get_store, run_operation
from .targeting import ResearchTarget, resolve_target

__all__ = [
    "ActionLogStore",
    "OperationError",
    "OperationResult",
    "ResearchTarget",
    "get_store",
    "resolve_target",
    "run_operation",
]
