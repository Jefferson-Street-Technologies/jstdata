"""Reusable interactive primitives for discoverable TUI controls.

Visual grammar: buttons and chips look clickable; accelerators sit beside the
control. Mouse and keyboard share the same action handlers on the host screen.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Label, Static


class ActionButton(Static, can_focus=True):
    """Primary/secondary button with optional shortcut hint.

    Click or Enter posts :class:`Pressed`.
    """

    DEFAULT_CSS = """
    ActionButton {
        width: auto;
        height: 1;
        padding: 0 1;
        color: #0a0a0a;
        background: #4ade80;
        text-style: bold;
        margin-right: 1;
    }
    ActionButton.-secondary {
        color: #e0e0e0;
        background: #1a1a1a;
        text-style: none;
        border: solid #333;
    }
    ActionButton.-ghost {
        color: #86efac;
        background: transparent;
        text-style: none;
    }
    ActionButton:focus {
        text-style: bold underline;
    }
    ActionButton.-disabled {
        color: #555;
        background: #151515;
    }
    """

    class Pressed(Message):
        def __init__(self, button_id: str) -> None:
            super().__init__()
            self.button_id = button_id

    def __init__(
        self,
        label: str,
        *,
        action_id: str,
        shortcut: str = "",
        variant: str = "primary",
        id: Optional[str] = None,
        disabled: bool = False,
    ) -> None:
        hint = f"  {shortcut}" if shortcut else ""
        super().__init__(f"{label}{hint}", id=id, markup=False)
        self.action_id = action_id
        self._label = label
        self._shortcut = shortcut
        self.disabled = disabled
        if variant == "secondary":
            self.add_class("-secondary")
        elif variant == "ghost":
            self.add_class("-ghost")
        if disabled:
            self.add_class("-disabled")

    def set_label(self, label: str, *, shortcut: Optional[str] = None) -> None:
        self._label = label
        if shortcut is not None:
            self._shortcut = shortcut
        hint = f"  {self._shortcut}" if self._shortcut else ""
        self.update(f"{self._label}{hint}")

    def on_click(self) -> None:
        if self.disabled or self.has_class("-disabled"):
            return
        self.post_message(self.Pressed(self.action_id))

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key == "enter" and not self.disabled:
            self.post_message(self.Pressed(self.action_id))
            event.stop()


class ResourceChip(Static, can_focus=True):
    """Selected resource with remove affordance: ``[ Label × ]``."""

    DEFAULT_CSS = """
    ResourceChip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: #e0e0e0;
        background: #1a2a1a;
        border: solid #2a4a2a;
    }
    ResourceChip:focus {
        background: #1a3a2a;
        border: solid #4ade80;
        text-style: bold;
    }
    """

    class Removed(Message):
        def __init__(self, kind: str, resource_id: str) -> None:
            super().__init__()
            self.kind = kind
            self.resource_id = resource_id

    def __init__(self, kind: str, resource_id: str, label: str) -> None:
        display = label if len(label) <= 28 else label[:25] + "…"
        super().__init__(f"{display} ×")
        self.kind = kind
        self.resource_id = resource_id
        self.resource_label = label

    def on_click(self) -> None:
        self.post_message(self.Removed(self.kind, self.resource_id))

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key in {"backspace", "delete", "enter"}:
            self.post_message(self.Removed(self.kind, self.resource_id))
            event.stop()


class ChipRow(Horizontal):
    """Horizontal chip strip with overflow (+N more) and an add control.

    Rebuild awaits ``remove_children`` before remounting — Textual defers
    removal, so a sync clear+mount collides on the add-button id.
    """

    DEFAULT_CSS = """
    ChipRow {
        height: 1;
        width: 1fr;
        layout: horizontal;
    }
    """

    MAX_VISIBLE = 3

    class Add(Message):
        def __init__(self, kind: str) -> None:
            super().__init__()
            self.kind = kind

    class Removed(Message):
        def __init__(self, kind: str, resource_id: str) -> None:
            super().__init__()
            self.kind = kind
            self.resource_id = resource_id

    def __init__(
        self,
        kind: str,
        *,
        add_label: str,
        add_shortcut: str = "",
        id: Optional[str] = None,
    ) -> None:
        super().__init__(id=id)
        self.kind = kind
        self._add_label = add_label
        self._add_shortcut = add_shortcut
        self._items: list[tuple[str, str]] = []
        self._rebuild_token = 0

    def set_items(self, items: Sequence[tuple[str, str]]) -> None:
        """``items`` are ``(id, label)`` pairs."""
        self._items = [(str(i), str(lab)) for i, lab in items]
        if self.is_mounted:
            self._schedule_rebuild()

    def on_mount(self) -> None:
        self._schedule_rebuild()

    def _schedule_rebuild(self) -> None:
        self._rebuild_token += 1
        token = self._rebuild_token
        self.run_worker(
            self._rebuild_chips(token),
            exclusive=True,
            group=f"chip-row-{id(self)}",
            name=f"chip-row-{self.kind}",
        )

    async def _rebuild_chips(self, token: int) -> None:
        await self.remove_children()
        if token != self._rebuild_token:
            return
        widgets: list = []
        visible = self._items[: self.MAX_VISIBLE]
        overflow = len(self._items) - len(visible)
        for rid, label in visible:
            widgets.append(ResourceChip(self.kind, rid, label))
        if overflow > 0:
            widgets.append(Label(f"[dim]+{overflow} more[/]", markup=True))
        widgets.append(
            ActionButton(
                self._add_label,
                action_id=f"add_{self.kind}",
                shortcut=self._add_shortcut,
                variant="ghost",
                id=f"chip-add-{self.kind}",
            )
        )
        if widgets:
            await self.mount(*widgets)

    def on_resource_chip_removed(self, event: ResourceChip.Removed) -> None:
        event.stop()
        self.post_message(self.Removed(event.kind, event.resource_id))

    def on_action_button_pressed(self, event: ActionButton.Pressed) -> None:
        if event.button_id == f"add_{self.kind}":
            event.stop()
            self.post_message(self.Add(self.kind))


class EnumSelect(Static, can_focus=True):
    """Compact left/right enum: ``[ Quarterly ▾ ]`` style."""

    DEFAULT_CSS = """
    EnumSelect {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: #e0e0e0;
        background: #1a1a1a;
        border: solid #333;
    }
    EnumSelect:focus {
        border: solid #4ade80;
        color: #86efac;
        text-style: bold;
    }
    """

    class Changed(Message):
        def __init__(self, select_id: str, value: Any) -> None:
            super().__init__()
            self.select_id = select_id
            self.value = value

    def __init__(
        self,
        options: Sequence[Any],
        *,
        labels: Optional[dict[Any, str]] = None,
        value: Any = None,
        id: Optional[str] = None,
    ) -> None:
        super().__init__("", id=id)
        self._options = list(options)
        self._labels = labels or {}
        self._index = 0
        if value in self._options:
            self._index = self._options.index(value)
        self._select_id = id or "enum"

    @property
    def value(self) -> Any:
        return self._options[self._index]

    def set_value(self, value: Any) -> None:
        if value in self._options:
            self._index = self._options.index(value)
        self._paint()

    def on_mount(self) -> None:
        self._paint()

    def _paint(self) -> None:
        raw = self.value
        label = self._labels.get(raw, "Any" if raw is None else str(raw))
        self.update(f"{label} ▾")

    def action_cursor_left(self) -> None:
        self._index = (self._index - 1) % len(self._options)
        self._paint()
        self.post_message(self.Changed(self._select_id, self.value))

    def action_cursor_right(self) -> None:
        self._index = (self._index + 1) % len(self._options)
        self._paint()
        self.post_message(self.Changed(self._select_id, self.value))

    def on_click(self) -> None:
        self.action_cursor_right()

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key in {"left", "h"}:
            self.action_cursor_left()
            event.stop()
        elif event.key in {"right", "l", "enter", "space"}:
            self.action_cursor_right()
            event.stop()
