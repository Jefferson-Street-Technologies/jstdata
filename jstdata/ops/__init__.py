"""Shared research operations layer (TUI + ``jst actions``).

Every supported research op flows through ``run_operation``, which appends to
the SQLite action log and (for cacheable / state-effect ops) writes artifacts.
"""

from .logstore import ActionLogStore
from .result import OperationError, OperationResult
from .runner import (
    get_store,
    reset_default_actor,
    resolve_actor,
    run_operation,
    set_default_actor,
)
from .targeting import ResearchTarget, resolve_target

__all__ = [
    "ActionLogStore",
    "OperationError",
    "OperationResult",
    "ResearchTarget",
    "get_store",
    "reset_default_actor",
    "resolve_actor",
    "resolve_target",
    "run_operation",
    "set_default_actor",
]
