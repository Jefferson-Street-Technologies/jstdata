"""Reusable investigation actions (analytical TUI screens).

::

    jst action ls
    jst action show console
    jst action add console --args '{"taxonomy":"country"}'
"""

from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    format_action_help,
    get_action,
    hydrate_labels,
    label_for,
    list_actions,
    parse_action_kwargs,
    register,
    resource_type_for_id,
)
from .help import HelpScreen
from . import console as _console  # noqa: F401
from . import discover as _discover  # noqa: F401
from . import rank as _rank  # noqa: F401

__all__ = [
    "ActionArgument",
    "ActionBinding",
    "ActionSpec",
    "HelpScreen",
    "format_action_help",
    "get_action",
    "hydrate_labels",
    "label_for",
    "list_actions",
    "parse_action_kwargs",
    "register",
    "resource_type_for_id",
]
