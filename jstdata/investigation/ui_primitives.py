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
        background: #262626;
        text-style: none;
        /* No border — height-1 borders render as empty grey slabs. */
    }
    ActionButton.-ghost {
        color: #86efac;
        background: transparent;
        text-style: none;
    }
    ActionButton.-tab {
        width: auto;
        color: #e5e5e5;
        background: #333333;
        text-style: none;
        padding: 0 2;
        margin-right: 1;
        height: 1;
    }
    ActionButton.-tab.-active {
        color: #0a0a0a;
        background: #86efac;
        text-style: bold;
    }
    ActionButton.-tab:focus {
        text-style: bold underline;
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
        elif variant == "tab":
            self.add_class("-tab")
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
    """Selected resource with remove affordance: ``Label ×``.

    No Textual ``border`` — on ``height: 1`` borders render as empty grey
    slabs and hide the label (the misrendered chip zones).
    """

    DEFAULT_CSS = """
    ResourceChip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: #e8e8e8;
        background: #1f2a1f;
        text-style: none;
    }
    ResourceChip:focus {
        color: #86efac;
        background: #243324;
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
        background: #0a0a0a;
    }
    ChipRow ActionButton.-ghost {
        background: #0a0a0a;
        color: #86efac;
    }
    ChipRow.-empty {
        display: none;
        height: 0;
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
        add_label: str = "+ Add",
        add_shortcut: str = "",
        show_add: bool = True,
        id: Optional[str] = None,
    ) -> None:
        super().__init__(id=id)
        self.kind = kind
        self._add_label = add_label
        self._add_shortcut = add_shortcut
        self._show_add = show_add
        self._items: list[tuple[str, str]] = []
        self._rebuild_token = 0
        self._rebuild_running = False

    def set_items(self, items: Sequence[tuple[str, str]]) -> None:
        """``items`` are ``(id, label)`` pairs."""
        self._items = [(str(i), str(lab)) for i, lab in items]
        if self.is_mounted:
            self._schedule_rebuild()

    def on_mount(self) -> None:
        self._schedule_rebuild()

    def _schedule_rebuild(self) -> None:
        self._rebuild_token += 1
        # Defer so we don't start a worker before the app event loop is live.
        self.call_later(self._launch_rebuild)

    def _launch_rebuild(self) -> None:
        if self.app is None or not self.is_mounted:
            return
        if self._rebuild_running:
            return  # in-flight loop will pick up the new token
        # Set the guard *before* scheduling so overlapping call_later
        # callbacks cannot start a second mount worker (triple-list bug).
        self._rebuild_running = True
        # Never exclusive-cancel DOM work — cancelling mid-remove_children
        # can abort the message pump and blank-quit the TUI.
        self.run_worker(
            self._rebuild_chips,
            exclusive=False,
            group=f"chip-row-{id(self)}",
            name=f"chip-row-{self.kind}",
            exit_on_error=False,
        )

    async def _rebuild_chips(self) -> None:
        try:
            while self.is_mounted:
                token = self._rebuild_token
                await self.remove_children()
                if not self.is_mounted:
                    return
                if token != self._rebuild_token:
                    continue
                widgets: list = []
                visible = self._items[: self.MAX_VISIBLE]
                overflow = len(self._items) - len(visible)
                for rid, label in visible:
                    widgets.append(ResourceChip(self.kind, rid, label))
                if overflow > 0:
                    widgets.append(Label(f"[dim]+{overflow} more[/]", markup=True))
                if self._show_add:
                    # No fixed widget id — action_id is enough; avoids DuplicateIds.
                    widgets.append(
                        ActionButton(
                            self._add_label,
                            action_id=f"add_{self.kind}",
                            shortcut=self._add_shortcut,
                            variant="ghost",
                        )
                    )
                if widgets:
                    await self.mount(*widgets)
                # Collapse when nothing to show (sidebar headers own the Add control).
                self.set_class(not widgets, "-empty")
                if token == self._rebuild_token:
                    return
        finally:
            self._rebuild_running = False

    def on_resource_chip_removed(self, event: ResourceChip.Removed) -> None:
        event.stop()
        self.post_message(self.Removed(event.kind, event.resource_id))

    def on_action_button_pressed(self, event: ActionButton.Pressed) -> None:
        if event.button_id == f"add_{self.kind}":
            event.stop()
            self.post_message(self.Add(self.kind))


class EnumSelect(Static, can_focus=True):
    """Compact left/right enum: ``Quarterly ▾`` style (borderless at height 1)."""

    DEFAULT_CSS = """
    EnumSelect {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: #a3a3a3;
        background: #0a0a0a;
    }
    EnumSelect:focus {
        color: #86efac;
        background: #141414;
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
        prefix: str = "",
    ) -> None:
        super().__init__("", id=id)
        self._options = list(options)
        self._labels = labels or {}
        self._prefix = prefix
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

    def set_options(
        self,
        options: Sequence[Any],
        *,
        labels: Optional[dict[Any, str]] = None,
        value: Any = ...,
    ) -> None:
        """Replace the option list.

        Pass ``value=...`` (default) to keep the current selection when it
        still exists; pass an explicit value (including ``None``) to select it.
        """
        if value is ...:
            prefer = self._options[self._index] if self._options else None
        else:
            prefer = value
        self._options = list(options) if options else [None]
        if labels is not None:
            self._labels = dict(labels)
        if prefer in self._options:
            self._index = self._options.index(prefer)
        else:
            self._index = 0
        self._paint()

    def on_mount(self) -> None:
        self._paint()

    def _paint(self) -> None:
        from rich.markup import escape

        raw = self.value
        label = self._labels.get(raw, "Any" if raw is None else str(raw))
        safe = escape(str(label))
        if self._prefix:
            # Match sidebar / chrome accents: bold light green label, muted value.
            self.update(
                f"[bold #86efac]{escape(self._prefix)}[/] {safe} ▾"
            )
        else:
            self.update(f"{safe} ▾")

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
