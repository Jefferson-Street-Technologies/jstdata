"""Shared research operations layer (TUI + ``jst actions``).

Every supported research op eventually flows through ``run_operation``.
Action-log persistence lands in a later slice; today the runner returns a
structured result with a placeholder event id.
"""

from .result import OperationError, OperationResult
from .runner import run_operation
from .targeting import ResearchTarget, resolve_target

__all__ = [
    "OperationError",
    "OperationResult",
    "ResearchTarget",
    "resolve_target",
    "run_operation",
]
