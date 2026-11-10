"""Shared option catalog for ``jst action add`` and ``:add``.

One source of truth for flag names, help text, and value-taking flags so the
CLI and command palette cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class AddOption:
    """One flag on ``action add`` / ``:add``."""

    flag: str
    help: str
    takes_value: bool = False
    """True when the next token is the option value (``--args``, ``--parent``)."""

    exclusive_with: Optional[str] = None
    """Partner flag that cannot coexist (``--activate`` / ``--no-activate``)."""

    # Click wiring. ``None`` → palette-only (not registered on the CLI).
    click_dest: Optional[str] = None
    click_type: Any = None
    click_is_flag: bool = False
    click_default: Any = None


# Order is completion / help order. Keep CLI and palette in sync via this tuple.
ADD_OPTIONS: tuple[AddOption, ...] = (
    AddOption(
        flag="--args",
        help='JSON object of action seed args, e.g. \'{"taxonomy":"country"}\'',
        takes_value=True,
        click_dest="args_json",
        click_default=None,
    ),
    AddOption(
        flag="--parent",
        help="Parent action id (default: active action)",
        takes_value=True,
        click_dest="parent_id",
        click_type=int,
        click_default=None,
    ),
    AddOption(
        flag="--activate",
        help="Make the new action active after creating it",
        exclusive_with="--no-activate",
        click_dest="activate",
        click_is_flag=True,
        click_default=False,
    ),
    AddOption(
        flag="--no-activate",
        help="Keep the current action active (palette default is activate)",
        exclusive_with="--activate",
    ),
)

ADD_FLAGS: tuple[str, ...] = tuple(o.flag for o in ADD_OPTIONS)
ADD_FLAG_DOCS: dict[str, str] = {o.flag: o.help for o in ADD_OPTIONS}
ADD_VALUE_FLAGS: frozenset[str] = frozenset(
    o.flag for o in ADD_OPTIONS if o.takes_value
)

ADD_USAGE = "add <type> [--args JSON] [--parent N] [--activate|--no-activate]"


def apply_click_add_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate a Click command with every option that has ``click_dest``."""
    import click

    for opt in reversed(ADD_OPTIONS):
        if opt.click_dest is None:
            continue
        kwargs: dict[str, Any] = {"help": opt.help}
        if opt.click_is_flag:
            kwargs["is_flag"] = True
            kwargs["default"] = bool(opt.click_default)
        else:
            kwargs["default"] = opt.click_default
            if opt.click_type is not None:
                kwargs["type"] = opt.click_type
        fn = click.option(opt.flag, opt.click_dest, **kwargs)(fn)
    return fn
