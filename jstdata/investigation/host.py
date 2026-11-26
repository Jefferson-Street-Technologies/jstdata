"""Investigation host: tree navigation, command mode, drawers, hot reload."""

from __future__ import annotations

from rich.markup import escape

import asyncio
import contextvars
import copy
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import NamedTuple, Optional, Sequence

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import Input, Label, Static, TextArea

from ..client import APP_DIR, ApiKeyNotSetError, JSTDataClient
from ..actions.base import ActionBinding, get_action, hydrate_labels
from ..actions.help import HelpScreen
from .active import ActiveContext, active_path, load_active, save_active
from .api import (
    add_action,
    add_query,
    create_investigation,
    remove_action,
    remove_query,
    persist_investigation,
    set_active,
    update_investigation,
    update_query,
)
from .bootstrap import ensure_bootstrap
from .command_palette import CommandPalette
from .commands import CommandError, ParsedCommand, parse_command_line
from .model import (
    DEFAULT_WORKSPACE_ID,
    Investigation,
    InvestigationError,
    QueryNote,
    ResearchContext,
    ROOT_ACTION_ID,
    Workspace,
    resolve_input_context,
)
from .ui_primitives import ActionButton
from .browsers import DestinationScreen, WorkspaceBrowser
from .home import HomeScreen
from .nav import DEST_IDS, parse_goto
from .publish_screens import (
    PublishConfirmModal,
    PublishDraftScreen,
    PublishedModal,
)
from .activity_drawer import ActivityDrawer
from .search_drawer import SearchDrawer
from .project_store import (
    duplicate_investigation,
    find_investigation_project,
    load_project,
    load_project_investigation,
    move_investigation,
    project_investigations_dir,
    remove_investigation_from_project,
    save_project_investigation,
)
from .project import Project
from .store import investigation_path, load_investigation
from .workspace_drawer import WorkspaceDrawer
from ..actions.drill import DrillDownModal
from ..actions.query_view import ObservationDetail
from ..onboarding import (
    CoachSnapshot,
    Tutorial,
    TutorialCoach,
    completed_tutorials,
    list_tutorials,
    load_tutorial,
    mark_completed,
    mark_prompted,
    set_tips_enabled,
    should_prompt,
    tip_for,
    tips_enabled,
)
from ..onboarding.tutorials import TUTORIAL_PREFIX
from ..onboarding.ui import (
    COMPLETE_BACK,
    COMPLETE_MENU,
    COMPLETE_STAY,
    LEAVE_TUTORIAL,
    CoachBar,
    TipBox,
    TutorialComplete,
    TutorialMenu,
)

from . import home as _home  # noqa: F401
import jstdata.actions  # noqa: F401

POLL_INTERVAL = 0.4
COACH_INTERVAL = 0.25


class InvalidStateScreen(Screen):
    """Shown when ``.active.yaml`` or the investigation cannot be resolved."""

    DEFAULT_CSS = """
    InvalidStateScreen {
        align: center middle;
        background: #0a0a0a;
    }
    #invalid-box {
        width: 72;
        height: auto;
        border: thick #f87171;
        background: #111111;
        padding: 1 2;
    }
    #invalid-title {
        color: #f87171;
        text-style: bold;
        margin-bottom: 1;
    }
    #invalid-body {
        color: #e0e0e0;
        height: auto;
    }
    #invalid-hint {
        color: #888;
        margin-top: 1;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="invalid-box"):
            yield Label("INVALID INVESTIGATION STATE", id="invalid-title")
            yield Static(self.message, id="invalid-body")
            yield Label(
                "Fix ~/.jstdata/.active.yaml or the investigation YAML, "
                "or press q to quit.",
                id="invalid-hint",
            )


class ConfirmForceModal(ModalScreen[bool]):
    """Ask whether to re-run a destructive command with --force."""

    DEFAULT_CSS = """
    ConfirmForceModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #confirm-box {
        width: 64;
        height: auto;
        border: thick #fbbf24;
        background: #111111;
        padding: 1 2;
    }
    #confirm-title { color: #fbbf24; text-style: bold; margin-bottom: 1; }
    #confirm-body { color: #e0e0e0; height: auto; margin-bottom: 1; }
    #confirm-hint { color: #888; }
    """

    BINDINGS = [
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("escape", "no", "No", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("CONFIRM", id="confirm-title")
            yield Static(self.message, id="confirm-body")
            yield Label("[bold]y[/bold] yes  ·  [bold]n[/bold]/[bold]esc[/bold] cancel", id="confirm-hint", markup=True)

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class QueryRenameModal(ModalScreen[str | None]):
    """Rename a saved query (display name only)."""

    DEFAULT_CSS = """
    QueryRenameModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #qrename-box {
        width: 64;
        height: auto;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #qrename-kicker { color: #4ade80; text-style: bold; margin-bottom: 1; }
    #qrename-name {
        width: 100%;
        background: #0a0a0a;
        border: solid #333;
        margin-bottom: 1;
    }
    #qrename-hint { color: #888; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "save", "Save", show=False, priority=True),
    ]

    def __init__(self, *, title: str, name: str = "") -> None:
        super().__init__()
        self._title = title
        self._name = name

    def compose(self) -> ComposeResult:
        with Vertical(id="qrename-box"):
            yield Label(self._title, id="qrename-kicker")
            yield Input(
                self._name,
                placeholder="Query name (defaults to metric · entity)…",
                id="qrename-name",
            )
            yield Label(
                "[bold]enter[/bold] save  ·  [bold]esc[/bold] cancel",
                id="qrename-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one("#qrename-name", Input).focus()

    @on(Input.Submitted, "#qrename-name")
    def on_submitted(self) -> None:
        self.action_save()

    def action_save(self) -> None:
        name = self.query_one("#qrename-name", Input).value.strip()
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class QueryContextMenu(ModalScreen[str | None]):
    """Contextual actions for a sidebar query: rename or delete."""

    DEFAULT_CSS = """
    QueryContextMenu {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #qctx-box {
        width: 42;
        height: auto;
        border: tall #525252;
        background: #111111;
        padding: 1 2;
    }
    #qctx-kicker { color: #a3a3a3; height: 1; margin-bottom: 1; }
    #qctx-title {
        color: #fafafa;
        text-style: bold;
        height: auto;
        max-height: 2;
        margin-bottom: 1;
    }
    #qctx-actions { height: 1; }
    #qctx-hint { color: #666; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("r", "rename", "Rename", show=False),
        Binding("d", "delete", "Delete", show=False),
        Binding("x", "delete", "Delete", show=False),
    ]

    def __init__(self, *, query_id: int, label: str) -> None:
        super().__init__()
        self.query_id = query_id
        self._label = label

    def compose(self) -> ComposeResult:
        from .ui_primitives import ActionButton

        with Vertical(id="qctx-box"):
            yield Label(f"Query {self.query_id}", id="qctx-kicker")
            yield Label(self._label or f"Query {self.query_id}", id="qctx-title")
            with Horizontal(id="qctx-actions"):
                yield ActionButton(
                    "Rename",
                    action_id="rename",
                    shortcut="r",
                    variant="secondary",
                    id="qctx-rename",
                )
                yield ActionButton(
                    "Delete",
                    action_id="delete",
                    shortcut="x",
                    variant="secondary",
                    id="qctx-delete",
                )
            yield Label(
                "[bold]esc[/bold] cancel",
                id="qctx-hint",
                markup=True,
            )

    def on_action_button_pressed(self, event) -> None:  # noqa: ANN001
        event.stop()
        if event.button_id == "rename":
            self.action_rename()
        elif event.button_id == "delete":
            self.action_delete()

    def action_rename(self) -> None:
        self.dismiss("rename")

    def action_delete(self) -> None:
        self.dismiss("delete")

    def action_cancel(self) -> None:
        self.dismiss(None)


class AnnotationModal(ModalScreen[str | None]):
    """Edit freeform investigation-level annotations (name + body)."""

    DEFAULT_CSS = """
    AnnotationModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #anno-box {
        width: 72;
        height: 24;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #anno-kicker { color: #4ade80; text-style: bold; height: 1; margin-bottom: 0; }
    #anno-scope {
        color: #888;
        height: 1;
        margin-bottom: 1;
    }
    #anno-name-label { color: #888; height: 1; }
    #anno-name {
        width: 100%;
        height: 3;
        background: #0a0a0a;
        border: solid #333;
        margin-bottom: 1;
    }
    #anno-body-label { color: #888; height: 1; }
    #anno-body {
        width: 100%;
        height: 1fr;
        background: #0a0a0a;
        border: solid #333;
        margin-bottom: 1;
    }
    #anno-hint { color: #888; height: 1; }
    """

    BINDINGS = [
        Binding("enter", "save", "Save", show=False, priority=True),
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, *, title: str, text: str = "") -> None:
        from .nav import unpack_annotation

        super().__init__()
        self._title = title
        # Avoid Textual Widget._name — use dedicated annotation fields.
        self._anno_name, self._anno_body = unpack_annotation(text)

    def compose(self) -> ComposeResult:
        with Vertical(id="anno-box"):
            yield Label(self._title, id="anno-kicker")
            yield Label(
                "Investigation commentary (whole tab — not tied to a series)",
                id="anno-scope",
            )
            yield Label("Name (optional)", id="anno-name-label")
            yield Input(
                value=self._anno_name,
                placeholder="Shown as a heading when set…",
                id="anno-name",
            )
            yield Label("Notes", id="anno-body-label")
            yield TextArea(self._anno_body, id="anno-body")
            yield Label(
                "[bold]enter[/bold] save  ·  [bold]shift+enter[/bold] newline  ·  "
                "[bold]esc[/bold] cancel",
                id="anno-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        if self._anno_name:
            self.query_one("#anno-body", TextArea).focus()
        else:
            self.query_one("#anno-name", Input).focus()

    def on_key(self, event) -> None:  # noqa: ANN001
        # Shift+Enter inserts a newline in the body; plain Enter always saves
        # (overrides TextArea's newline / Input submit).
        if event.key == "shift+enter":
            body = self.query_one("#anno-body", TextArea)
            body.insert("\n")
            event.stop()
            event.prevent_default()
            return
        if event.key == "enter":
            self.action_save()
            event.stop()
            event.prevent_default()

    def action_save(self) -> None:
        from .nav import pack_annotation

        name = self.query_one("#anno-name", Input).value
        body = self.query_one("#anno-body", TextArea).text
        self.dismiss(pack_annotation(name, body))

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass
class QueryAnnotationEdit:
    """Result of the query annotation editor (commentary + notes_on)."""

    annotations: str
    notes_on: list[QueryNote] = field(default_factory=list)


class QueryAnnotationModal(ModalScreen[QueryAnnotationEdit | None]):
    """Edit all query annotation forms: freeform commentary and notes_on."""

    DEFAULT_CSS = """
    QueryAnnotationModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.45);
    }
    #qanno-box {
        width: 84;
        height: 36;
        max-height: 90%;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #qanno-kicker {
        color: #4ade80;
        text-style: bold;
        height: 1;
    }
    #qanno-scope {
        color: #888;
        height: 2;
        margin-bottom: 1;
    }
    #qanno-section-commentary, #qanno-section-notes {
        color: #e5e5e5;
        text-style: bold;
        height: 1;
        margin-top: 0;
        margin-bottom: 0;
    }
    #qanno-name-label, #qanno-body-label, #qanno-add-label {
        color: #888;
        height: 1;
    }
    #qanno-name, #qanno-note-text, #qanno-note-series,
    #qanno-note-from, #qanno-note-to {
        width: 100%;
        background: #0a0a0a;
        border: solid #333;
    }
    #qanno-name { height: 3; margin-bottom: 0; }
    #qanno-body {
        width: 100%;
        height: 6;
        background: #0a0a0a;
        border: solid #333;
        margin-bottom: 1;
    }
    #qanno-notes-list {
        height: auto;
        min-height: 3;
        max-height: 8;
        background: #0a0a0a;
        border: solid #333;
        padding: 0 1;
        margin-bottom: 1;
        color: #e0e0e0;
    }
    #qanno-add-row {
        height: 1;
        margin-bottom: 0;
    }
    #qanno-note-text { width: 1fr; height: 3; }
    #qanno-note-series { width: 1fr; height: 3; }
    #qanno-dates { height: 3; }
    #qanno-note-from, #qanno-note-to { width: 1fr; height: 3; }
    #qanno-add-btn { margin-top: 0; }
    #qanno-error {
        color: #f87171;
        height: 1;
        margin-top: 0;
    }
    #qanno-hint { color: #888; height: 1; margin-top: 1; }
    #qanno-series-hint {
        color: #666;
        height: 1;
        margin-bottom: 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        *,
        title: str,
        text: str = "",
        notes_on: Sequence[QueryNote] | None = None,
        series_choices: Sequence[str] | None = None,
    ) -> None:
        from .nav import unpack_annotation

        super().__init__()
        self._title = title
        # Avoid Textual Widget._name — use dedicated annotation fields.
        self._anno_name, self._anno_body = unpack_annotation(text)
        self._notes: list[QueryNote] = [
            QueryNote(
                text=n.text,
                series=n.series,
                from_=n.from_,
                to=n.to,
            )
            for n in (notes_on or [])
        ]
        self._series_choices = [s for s in (series_choices or []) if s]

    def compose(self) -> ComposeResult:
        with Vertical(id="qanno-box"):
            yield Label(self._title, id="qanno-kicker")
            yield Label(
                "Two kinds: freeform commentary on the query, and notes pinned "
                "to a series and/or date range (notes_on).",
                id="qanno-scope",
            )
            yield Label("1 · Query commentary", id="qanno-section-commentary")
            yield Label("Name (optional heading)", id="qanno-name-label")
            yield Input(
                value=self._anno_name,
                placeholder="e.g. Takeaway, Risk, Open question…",
                id="qanno-name",
            )
            yield Label("Body", id="qanno-body-label")
            yield TextArea(self._anno_body, id="qanno-body")
            yield Label("2 · Notes on series / periods", id="qanno-section-notes")
            yield Static("", id="qanno-notes-list", markup=True)
            if self._series_choices:
                preview = ", ".join(self._series_choices[:4])
                more = "…" if len(self._series_choices) > 4 else ""
                yield Label(
                    f"Series on this query: {preview}{more}",
                    id="qanno-series-hint",
                )
            yield Label(
                "Add note (needs text + series and/or from/to)",
                id="qanno-add-label",
            )
            yield Input(
                placeholder="Note text…",
                id="qanno-note-text",
            )
            yield Input(
                placeholder="Series id (optional)",
                id="qanno-note-series",
            )
            with Horizontal(id="qanno-dates"):
                yield Input(placeholder="From YYYY-MM-DD", id="qanno-note-from")
                yield Input(placeholder="To YYYY-MM-DD", id="qanno-note-to")
            with Horizontal(id="qanno-add-row"):
                yield ActionButton(
                    "+ Add note",
                    action_id="add_note",
                    variant="secondary",
                    id="qanno-add-btn",
                )
                yield ActionButton(
                    "Remove last",
                    action_id="rm_last_note",
                    variant="ghost",
                    id="qanno-rm-btn",
                )
            yield Label("", id="qanno-error")
            yield Label(
                "[bold]ctrl+s[/bold] save all  ·  "
                "[bold]shift+enter[/bold] newline in body  ·  "
                "[bold]esc[/bold] cancel",
                id="qanno-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self._rebuild_notes_list()
        self.query_one("#qanno-name", Input).focus()

    def _rebuild_notes_list(self) -> None:
        listing = self.query_one("#qanno-notes-list", Static)
        if not self._notes:
            listing.update("[dim]No series/period notes yet.[/]")
            return
        lines: list[str] = []
        for i, note in enumerate(self._notes):
            span = ""
            if note.from_ or note.to:
                span = f" · {note.from_ or '…'}–{note.to or '…'}"
            series = f" · {note.series}" if note.series else ""
            text = note.text.replace("\n", " ")
            if len(text) > 56:
                text = text[:55] + "…"
            lines.append(
                f"[bold #86efac]{i + 1:02d}[/] {escape(text)}"
                f"[dim]{escape(series + span)}[/]"
            )
        listing.update("\n".join(lines))

    def _set_error(self, message: str = "") -> None:
        self.query_one("#qanno-error", Label).update(message)

    def _add_note_from_form(self) -> None:
        text = self.query_one("#qanno-note-text", Input).value
        series = self.query_one("#qanno-note-series", Input).value
        date_from = self.query_one("#qanno-note-from", Input).value
        date_to = self.query_one("#qanno-note-to", Input).value
        payload = {
            "text": text,
            "series": series.strip() or None,
            "from": date_from.strip() or None,
            "to": date_to.strip() or None,
        }
        # Drop nulls so from_dict validation matches CLI annotate.
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            note = QueryNote.from_dict(payload, label="note")
        except InvestigationError as exc:
            self._set_error(str(exc))
            return
        self._notes.append(note)
        self._set_error("")
        self.query_one("#qanno-note-text", Input).value = ""
        self.query_one("#qanno-note-series", Input).value = ""
        self.query_one("#qanno-note-from", Input).value = ""
        self.query_one("#qanno-note-to", Input).value = ""
        self._rebuild_notes_list()
        self.query_one("#qanno-note-text", Input).focus()

    def on_action_button_pressed(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "add_note":
            self._add_note_from_form()
            return
        if event.button_id == "rm_last_note":
            if self._notes:
                self._notes.pop()
                self._rebuild_notes_list()
                self._set_error("")
            else:
                self._set_error("No series/period notes to remove")

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key == "shift+enter":
            focused = self.focused
            if isinstance(focused, TextArea):
                focused.insert("\n")
                event.stop()
                event.prevent_default()
            return
        if event.key == "enter":
            focused = self.focused
            # Enter in the add-note fields appends; elsewhere saves.
            if isinstance(focused, Input) and focused.id in {
                "qanno-note-text",
                "qanno-note-series",
                "qanno-note-from",
                "qanno-note-to",
            }:
                self._add_note_from_form()
                event.stop()
                event.prevent_default()
                return
            if isinstance(focused, TextArea) and focused.id == "qanno-body":
                # Don't save on Enter in body — use ctrl+s (shift+enter = newline).
                return
            self.action_save()
            event.stop()
            event.prevent_default()

    def action_save(self) -> None:
        from .nav import pack_annotation

        name = self.query_one("#qanno-name", Input).value
        body = self.query_one("#qanno-body", TextArea).text
        self.dismiss(
            QueryAnnotationEdit(
                annotations=pack_annotation(name, body),
                notes_on=list(self._notes),
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class PublishOutcome(NamedTuple):
    """How a TUI publish ended: a link, or an error to show."""

    url: Optional[str]
    error: Optional[str]
    updated: bool


class InvestigationHost(App):
    """TUI host bound to one investigation file + ``.active.yaml``."""

    CSS = """
    Screen {
        background: #0a0a0a;
        color: #e0e0e0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("escape", "back", "Back"),
        Binding("question_mark", "show_shortcuts_hint", "Shortcuts", key_display="?"),
        Binding(":", "command_palette", "Command", key_display=":"),
        Binding("H", "go_home", "Home", key_display="H"),
        # Tree depth via arrows. Sibling nav on Ctrl+n/p; ``N`` is new tab.
        Binding("ctrl+n", "next_sibling", "Next sibling", show=False),
        Binding("ctrl+p", "prev_sibling", "Prev sibling", show=False),
        Binding("down", "next_sibling", "Next sibling", show=False),
        Binding("up", "prev_sibling", "Prev sibling", show=False),
        Binding("N", "new_investigation_tab", "New tab", key_display="N"),
        Binding("P", "parent_action", "To parent", key_display="P"),
        Binding("right", "child_action", "Into child", show=False),
        Binding("left", "parent_action", "To parent", show=False),
        Binding("t", "tutorials", "Tutorials", key_display="t"),
        Binding("T", "tab_jump", "Jump tabs", key_display="T"),
        # Investigation tabs within the open project (`{` / `}`).
        Binding("{", "prev_investigation", "Prev investigation tab", show=False),
        Binding("}", "next_investigation", "Next investigation tab", show=False),
        Binding("L", "show_activity", "Activity", key_display="L"),
        Binding("slash", "universal_search", "Search", show=False),
        Binding("F", "feedback_prompt", "Feedback", key_display="F"),
        Binding("exclamation_mark", "bug_report", "Bug report", key_display="!"),
        Binding("s", "save_active_query", "Save query", show=False),
        # Shift+X removes the open query/resource (lowercase x = chart toggle).
        Binding("X", "remove_active_sidebar_target", "Remove tile", show=False),
        # UI navigation history (distinct from action-log / H history).
        Binding("ctrl+o", "nav_back", "Back", show=False),
        Binding("ctrl+i", "nav_forward", "Forward", show=False),
        # ``dd`` is handled in on_key — Textual commas mean alternate keys, not chords.
    ]

    # Seconds to wait for the second ``d`` (vim-style).
    _DD_TIMEOUT = 0.45
    # Seconds a second ``q`` has to quit while publishing (JEF-170).
    _QUIT_AGAIN_TIMEOUT = 5.0

    def __init__(
        self,
        client: JSTDataClient,
        *,
        app_dir: Optional[Path] = None,
        start_tutorial: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.app_dir = app_dir
        self.project_id: str | None = None
        self.project: Project | None = None
        self.investigation: Investigation | None = None
        self.active: ActiveContext | None = None
        self.workspace = Workspace()
        self.input_context = ResearchContext()
        self.labels: dict[str, str] = {}
        self._order: list[int] = []
        self._ws_order: list[int] = []
        # investigation_id → display title for tab chrome
        self._inv_titles: dict[str, str] = {}
        # investigation_id → activity mark (●, !, …)
        self._tab_activity_marks: dict[str, str] = {}
        # Per-tab resume: investigation_id → (workspace_id, action_id)
        self._inv_resume: dict[str, tuple[int, int]] = {}
        self._inv_mtime: float | None = None
        self._active_mtime: float | None = None
        self._own_active_mtime: float | None = None
        self._ws_fingerprint: tuple | None = None
        self._invalid = False
        self._suppress_poll = False
        self._command_history: list[str] = []
        self._pending_d = False
        self._d_timer: Timer | None = None
        self._pending_command: ParsedCommand | None = None
        self._pending_goto: str | None = None
        self._pending_inline_edit: str | None = None
        self._publishing = False
        # The publish request runs on its own daemon thread, so quitting
        # mid-publish doesn't wait for it (JEF-170). If the TUI closes first,
        # run_investigation_host waits for the thread and prints the outcome.
        self.publish_thread: threading.Thread | None = None
        self.publish_outcome: PublishOutcome | None = None
        self._quitting = False
        self._quit_armed_at: float | None = None
        self._nav_dest: str | None = None
        self._nav_return: str | None = None
        self._dest_index: dict[str, int] = {}
        # Browser-style view history (action ids). Not the research action log.
        self._ui_nav_back: list[tuple[str, int]] = []
        self._ui_nav_fwd: list[tuple[str, int]] = []
        self._ui_nav_silent = False
        # Onboarding: running tutorial coach + where to return on leave.
        self._initial_tutorial = load_tutorial(start_tutorial) if start_tutorial else None
        self.coach: TutorialCoach | None = None
        self._pre_tutorial: tuple[str, int, str | None] | None = None
        self._coach_bar: CoachBar | None = None
        self._coach_screen: Screen | None = None
        self._coach_shown: tuple[str, str, str] | None = None
        # Pop-out tip box (outside tutorials); loaded from state on mount.
        self._tips_on = False
        self._tip_bar: TipBox | None = None
        self._tip_screen: Screen | None = None
        self._tip_shown: str | None = None

    def _active_file(self) -> Path:
        return active_path(app_dir=self.app_dir)

    def _inv_root(self) -> Path | None:
        """Investigations directory for the active project (if resolved)."""
        if self.project_id is None:
            return None
        return project_investigations_dir(self.project_id, app_dir=self.app_dir)

    def _inv_file(self) -> Path:
        assert self.active is not None
        root = self._inv_root()
        if root is None:
            raise InvestigationError("No active project to resolve investigation path.")
        return investigation_path(self.active.investigation, root=root)

    @property
    def onboarding_dir(self) -> Path:
        return self.app_dir or APP_DIR

    def on_mount(self) -> None:
        ensure_bootstrap(app_dir=self.app_dir)
        self._reload_everything(force_screen=True)
        self.set_interval(POLL_INTERVAL, self._poll_files)
        self._tips_on = tips_enabled(self.onboarding_dir)
        self.set_interval(COACH_INTERVAL, self._poll_coach)
        self.set_interval(5.0, self._poll_activity_marks)
        if self._invalid:
            return
        first_run = should_prompt(self.onboarding_dir)
        if self._initial_tutorial is not None:
            mark_prompted(self.onboarding_dir)
            self.start_tutorial(self._initial_tutorial)
        elif first_run:
            mark_prompted(self.onboarding_dir)
            self.call_after_refresh(self._open_tutorial_menu_screen, first_run)

    def remember_label(self, resource_id: str, label: str) -> None:
        self.labels[resource_id] = label or resource_id

    def persist_active_workspace(self) -> None:
        """Flush workspace mutations into the investigation YAML if dirty."""
        self._persist_workspace_if_dirty()

    def refresh_workspace_drawer(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, WorkspaceDrawer):
                screen.workspace = self.workspace
                screen.labels = self.labels
                screen.rebuild()
            if isinstance(screen, WorkspaceBrowser):
                screen.workspace = self.workspace
                screen.labels = self.labels
                screen.rebuild()
            if isinstance(screen, HomeScreen):
                screen.refresh_live()

    @property
    def action_count(self) -> int:
        return max(len(self._order), 1)

    @property
    def action_index(self) -> int:
        if not self.active or not self._order:
            return 0
        try:
            return self._order.index(self.active.action)
        except ValueError:
            return 0

    @property
    def current_action_spec(self):
        if not self.investigation or not self.active:
            return get_action("home")
        try:
            action = self.investigation.get_action(self.active.action)
            return get_action(action.type)
        except (InvestigationError, KeyError):
            return get_action("home")

    def _snapshot_mtimes(self) -> None:
        try:
            self._active_mtime = self._active_file().stat().st_mtime
        except OSError:
            self._active_mtime = None
        if self.active is not None:
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                self._inv_mtime = None

    def _reload_everything(self, *, force_screen: bool = False) -> None:
        try:
            self.active = load_active(app_dir=self.app_dir)
            project_id = self.active.project
            if project_id is None:
                project_id = find_investigation_project(
                    self.active.investigation, app_dir=self.app_dir
                )
            if project_id is None:
                raise InvestigationError(
                    f"Investigation {self.active.investigation!r} "
                    "not found in any project."
                )
            self.project_id = project_id
            self.project = load_project(project_id, app_dir=self.app_dir)
            self._refresh_inv_titles()
            if self.active.project != project_id:
                self.active.project = project_id
                save_active(self.active, app_dir=self.app_dir)
            self.investigation = load_project_investigation(
                project_id,
                self.active.investigation,
                app_dir=self.app_dir,
            )
            self.investigation.validate()
            ws = self.investigation.get_workspace(self.active.workspace)
            _ = self.investigation.get_action(self.active.action)
            self._apply_workspace(ws)
            self._order = self.investigation.bfs_order()
            self._ws_order = self.investigation.workspace_order()
            self._invalid = False
            self._snapshot_mtimes()
            if force_screen or not isinstance(self.screen, Screen):
                self._show_current_action(reset=True)
            else:
                self._show_current_action(reset=force_screen)
        except (InvestigationError, OSError, KeyError) as exc:
            self._enter_invalid(str(exc))

    def _apply_workspace(self, ws: Workspace) -> None:
        # Hold the live investigation workspace object; screens mutate it in place.
        self.workspace = ws
        self._ws_fingerprint = ws.resource_fingerprint()
        self.labels = {}
        # Hydrate off the UI thread — /resource can stall and freeze Textual.
        self.run_worker(
            self._hydrate_labels_worker,
            exclusive=True,
            group="hydrate-labels",
            thread=True,
        )
        self.refresh_workspace_drawer()

    def _query_resource_typed_ids(self) -> list[tuple[str, str]]:
        """``(type, id)`` for every query resource in the open investigation."""
        out: list[tuple[str, str]] = []
        if self.investigation is None:
            return out
        for query in self.investigation.queries.values():
            for rid in query.metric:
                out.append(("metric", rid))
            for rid in query.entity:
                out.append(("entity", rid))
            for rid in query.series:
                out.append(("series", rid))
        return out

    def _hydrate_labels_worker(self) -> None:
        labels: dict[str, str] = {}
        try:
            hydrate_labels(
                self.client,
                self.workspace,
                labels,
                extra_ids=self._query_resource_typed_ids(),
            )
        except OSError as exc:
            msg = f"Label cache unavailable: {exc}"
            self.call_from_thread(lambda: self.notify(msg, severity="warning"))
            return
        self.call_from_thread(self._apply_hydrated_labels, labels)

    def _apply_hydrated_labels(self, labels: dict[str, str]) -> None:
        self.labels.update(labels)
        # Compose may still be attaching Home chrome when /resource returns.
        self.call_later(self.refresh_workspace_drawer)
        self.call_later(self.refresh_shell_chrome)

    def _hydrate_missing_labels(self, typed: list[tuple[str, str]]) -> None:
        """Background-fetch labels for query resources not yet in ``self.labels``."""
        pending = [(t, i) for t, i in typed if i and i not in self.labels]
        if not pending:
            return

        def _worker() -> None:
            from ..resource_cache import default_resource_cache

            labels: dict[str, str] = {}
            try:
                default_resource_cache().hydrate_ids(
                    self.client, pending, labels
                )
            except OSError:
                return
            if labels:
                self.call_from_thread(self._apply_hydrated_labels, labels)

        self.run_worker(
            _worker,
            exclusive=False,
            group="hydrate-query-labels",
            name="hydrate-query-labels",
            thread=True,
        )

    def _enter_invalid(self, message: str) -> None:
        self._invalid = True
        self.investigation = None
        self.project = None
        self._inv_titles = {}
        self._order = []
        self._ws_order = []
        while len(self.screen_stack) > 1:
            self.pop_screen()
        err = InvalidStateScreen(message)
        if isinstance(self.screen, InvalidStateScreen):
            self.switch_screen(err)
        else:
            self.push_screen(err)

    def _show_current_action(self, *, reset: bool = False) -> None:
        self._show_front(reset=reset)

    def _show_front(self, *, reset: bool = False) -> None:
        if self._invalid or not self.investigation or not self.active:
            return
        if isinstance(
            self.screen,
            (
                CommandPalette,
                ConfirmForceModal,
                DrillDownModal,
                ObservationDetail,
            ),
        ):
            # Command dispatch should already be deferred past dismiss; avoid
            # recursive call_after_refresh loops if a modal is still up.
            self.set_timer(0.05, self._show_front)
            return
        screen = self._make_action_screen()
        self._pop_transient_overlays()
        if len(self.screen_stack) <= 1:
            self.push_screen(screen)
        else:
            # Replace the action screen (index 1); keep drawers above if any.
            # Simpler V2: drop drawers on action switch.
            while len(self.screen_stack) > 2:
                self.pop_screen()
            self.switch_screen(screen)
        self.call_later(self.refresh_shell_chrome)

    def remember_dest_index(self, dest: str, index: int) -> None:
        self._dest_index[dest] = index

    def _leave_destination_for_action(self) -> None:
        if self._nav_dest:
            self._nav_return = self._nav_dest
        self._nav_dest = None

    def leave_destination(self) -> None:
        """Undrill Home to the destination menu without leaving the shell."""
        self._nav_dest = None
        if isinstance(self.screen, HomeScreen):
            self.screen.leave_destination()

    def open_destination(self, dest: str) -> None:
        if dest not in DEST_IDS:
            self.notify(f"Unknown destination {dest!r}", severity="warning")
            return
        self._nav_dest = dest
        self._nav_return = dest
        if isinstance(self.screen, HomeScreen):
            self.screen.enter_destination(dest)
            return
        if self.active and self.active.action != ROOT_ACTION_ID:
            self._write_active(self._make_active(action=ROOT_ACTION_ID))
        self._show_front()

    def open_action_from_nav(self, action_id: int) -> None:
        if self._invalid or not self.investigation:
            return
        if action_id not in self.investigation.actions:
            self.notify(f"Unknown action {action_id}", severity="error")
            return
        self._activate_action(action_id)

    def open_resource_view(
        self,
        resource_type: str,
        resource_id: str,
        *,
        label: str | None = None,
    ) -> None:
        name = label or resource_id
        if resource_type == "taxonomy":
            self.workspace.taxonomy = resource_id
            self.remember_label(resource_id, name)
            self.persist_active_workspace()
            self.notify(f"Taxonomy set to {resource_id}")
            return
        if resource_type == "metric":
            self.open_child_view(
                "metric",
                context=ResearchContext(metric=[resource_id]),
                description=f"Metric {name}",
            )
        elif resource_type == "entity":
            self.open_child_view(
                "entity",
                context=ResearchContext(entity=[resource_id]),
                description=f"Entity {name}",
            )
        elif resource_type == "release":
            self.open_child_view(
                "release",
                context=ResearchContext(release=[resource_id]),
                description=f"Release {name}",
            )
        elif resource_type == "series":
            self.open_child_view(
                "series",
                context=ResearchContext(series=[resource_id]),
                args={"series_id": resource_id},
                description=f"Series {name}",
            )
        else:
            self.open_child_view(
                "table",
                context=ResearchContext(series=[resource_id]),
                description=f"Series {name}",
            )

    def _make_action_screen(self) -> Screen:
        assert self.investigation is not None and self.active is not None
        action = self.investigation.get_action(self.active.action)
        ws = self.investigation.get_workspace(self.active.workspace)
        try:
            spec = get_action(action.type)
        except KeyError as exc:
            raise InvestigationError(
                f"Unknown action type {action.type!r} (action {action.id})."
            ) from exc

        self.input_context = resolve_input_context(ws, action.context)
        kwargs = dict(action.args)
        kwargs["input_context"] = self.input_context
        if action.type == "home":
            # User (or cloned) text shown as markup: escape it.
            kwargs.setdefault("title", escape(self.investigation.title))
            body = escape(action.description or "")
            if ws.description:
                body = (body + "\n\n" if body else "") + (
                    f"Workspace: {escape(ws.description)}"
                )
            kwargs.setdefault("body", body)

        return spec.create_screen(self.client, self.workspace, **kwargs)

    def open_child_view(
        self,
        action_type: str,
        *,
        context: Optional[ResearchContext] = None,
        args: Optional[dict] = None,
        description: Optional[str] = None,
    ) -> None:
        """Persist a child View under the active action and activate it."""
        if self._invalid or not self.investigation or not self.active:
            self.notify("No investigation loaded", severity="error")
            return
        self._record_ui_nav()
        self._leave_destination_for_action()
        try:
            action = add_action(
                self.investigation,
                action_type,
                parent_id=self.active.action,
                args=args or {},
                description=description,
                context=context,
                activate=True,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._order = self.investigation.bfs_order()
        self.active = load_active(app_dir=self.app_dir)
        self.notify(f"Opened {action.type} {action.id}")
        self._show_current_action(reset=True)

    def _pop_transient_overlays(self) -> None:
        """Dismiss command palette / confirm; leave drawers alone when possible."""
        while len(self.screen_stack) > 2 and isinstance(
            self.screen,
            (
                CommandPalette,
                ConfirmForceModal,
                DrillDownModal,
                ObservationDetail,
            ),
        ):
            self.pop_screen()

    def _make_active(
        self,
        *,
        investigation: str | None = None,
        workspace: int | None = None,
        action: int | None = None,
    ) -> ActiveContext:
        """Build ActiveContext, always stamping the current project id."""
        assert self.active is not None
        return ActiveContext(
            investigation=(
                investigation
                if investigation is not None
                else self.active.investigation
            ),
            workspace=(
                workspace if workspace is not None else self.active.workspace
            ),
            action=action if action is not None else self.active.action,
            project=self.project_id,
        )

    def _write_active(self, ctx: ActiveContext) -> None:
        if ctx.project is None and self.project_id is not None:
            ctx = ActiveContext(
                investigation=ctx.investigation,
                workspace=ctx.workspace,
                action=ctx.action,
                project=self.project_id,
            )
        self._suppress_poll = True
        try:
            save_active(ctx, app_dir=self.app_dir)
            self.active = ctx
            try:
                self._own_active_mtime = self._active_file().stat().st_mtime
                self._active_mtime = self._own_active_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False

    def _refresh_inv_titles(self) -> None:
        """Cache investigation titles for tab chrome (best-effort)."""
        titles: dict[str, str] = {}
        if self.project is None or self.project_id is None:
            self._inv_titles = titles
            return
        for inv_id in self.project.investigations:
            try:
                inv = load_project_investigation(
                    self.project_id, inv_id, app_dir=self.app_dir
                )
                titles[inv_id] = (inv.title or inv_id).strip() or inv_id
            except (InvestigationError, OSError, KeyError):
                titles[inv_id] = inv_id
        self._inv_titles = titles

    def investigation_tab_chips(self) -> list[str]:
        """Rich markup chips for the investigation tab strip (footer)."""
        if self.project is None or self.active is None:
            return []
        order = self.project.investigations
        if len(order) < 2:
            # Single-tab projects: still show the active inv id quietly.
            label = self._inv_titles.get(
                self.active.investigation, self.active.investigation
            )
            return [escape(label)]
        chips: list[str] = []
        for inv_id in order:
            label = self._inv_titles.get(inv_id, inv_id)
            safe = escape(label)
            if inv_id == self.active.investigation:
                chips.append(f"[bold #4ade80][{safe}][/]")
            else:
                chips.append(safe)
        return chips

    @staticmethod
    def cycle_investigation_id(
        order: list[str], current: str, delta: int
    ) -> str | None:
        """Return the next investigation id in ``order``, or None if unusable."""
        if len(order) < 2:
            return None
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0
        return order[(idx + delta) % len(order)]

    def _cycle_investigation(self, delta: int) -> None:
        if self._invalid or not self.project or not self.active:
            self.notify("No project loaded", severity="warning")
            return
        order = list(self.project.investigations)
        if len(order) < 2:
            self.notify("Only one investigation in this project", severity="warning")
            return
        target = self.cycle_investigation_id(
            order, self.active.investigation, delta
        )
        if target is None or target == self.active.investigation:
            return
        self._inv_resume[self.active.investigation] = (
            self.active.workspace,
            self.active.action,
        )
        workspace, action = self._inv_resume.get(
            target, (DEFAULT_WORKSPACE_ID, ROOT_ACTION_ID)
        )
        self._switch_investigation(target, action, workspace=workspace)
        label = self._inv_titles.get(target, target)
        self.notify(f"Investigation {label}", timeout=2)

    def action_next_investigation(self) -> None:
        self._cycle_investigation(1)

    def action_prev_investigation(self) -> None:
        self._cycle_investigation(-1)

    def _persist_workspace_if_dirty(self) -> None:
        if self._invalid or not self.investigation or not self.active:
            return
        try:
            ws = self.investigation.get_workspace(self.active.workspace)
        except InvestigationError:
            return
        # Screens mutate ``self.workspace`` in place (same object as ``ws``).
        if ws.resource_fingerprint() == self._ws_fingerprint:
            return
        self._suppress_poll = True
        try:
            persist_investigation(self.investigation, app_dir=self.app_dir)
            self._ws_fingerprint = ws.resource_fingerprint()
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False

    def _poll_files(self) -> None:
        if self._suppress_poll:
            return
        self._persist_workspace_if_dirty()

        active_changed = False
        inv_changed = False
        try:
            am = self._active_file().stat().st_mtime
        except OSError:
            self._enter_invalid(f"Missing active context at {self._active_file()}.")
            return
        if self._active_mtime is not None and am != self._active_mtime:
            if self._own_active_mtime is not None and am == self._own_active_mtime:
                self._active_mtime = am
            else:
                active_changed = True

        if self.active is not None:
            try:
                im = self._inv_file().stat().st_mtime
            except OSError:
                self._enter_invalid(
                    f"Investigation file missing: {self._inv_file()}."
                )
                return
            if self._inv_mtime is not None and im != self._inv_mtime:
                inv_changed = True

        if not active_changed and not inv_changed:
            return

        prev_action = self.active.action if self.active else None
        prev_ws = self.active.workspace if self.active else None
        prev_inv = self.active.investigation if self.active else None
        self._reload_everything(force_screen=False)
        if self._invalid or not self.active:
            return
        if (
            inv_changed
            or self.active.action != prev_action
            or self.active.workspace != prev_ws
            or self.active.investigation != prev_inv
        ):
            self._show_current_action(reset=True)
        self.notify("Investigation reloaded", timeout=2)

    def action_next_sibling(self) -> None:
        self._navigate_sibling(+1)

    def action_prev_sibling(self) -> None:
        self._navigate_sibling(-1)

    def action_child_action(self) -> None:
        self._navigate_deeper()

    def action_parent_action(self) -> None:
        self._navigate_shallower()

    def _clear_pending_d(self) -> None:
        self._pending_d = False
        if self._d_timer is not None:
            self._d_timer.stop()
            self._d_timer = None

    def _screen_action_for_key(self, key: str) -> str | None:
        """Return the focused screen's binding action name for ``key``, if any."""
        bindings = getattr(self.screen, "BINDINGS", None) or ()
        for binding in bindings:
            if getattr(binding, "key", None) == key:
                action = getattr(binding, "action", None)
                if isinstance(action, str) and action:
                    # Textual allows ``action`` or ``action(params)``.
                    return action.split("(", 1)[0]
        return None

    def _expire_pending_d(self) -> None:
        """First ``d`` timed out with no second — forward to the screen binding.

        Host owns ``dd`` (delete). Screens like entity view also bind ``d``
        (toggle relation direction); a lone ``d`` must reach them after the
        chord window closes.
        """
        if not self._pending_d:
            return
        self._pending_d = False
        self._d_timer = None
        action_name = self._screen_action_for_key("d")
        if not action_name:
            return
        method = getattr(self.screen, f"action_{action_name}", None)
        if callable(method):
            method()

    def _delete_action_allowed(self) -> bool:
        """Same gates as check_action for delete — used by the ``dd`` chord."""
        if self._invalid or not self.active or not self.investigation:
            return False
        if isinstance(self.focused, Input):
            return False
        if isinstance(
            self.screen,
            (
                WorkspaceDrawer,
                SearchDrawer,
                ActivityDrawer,
                CommandPalette,
                DrillDownModal,
                DestinationScreen,
                HomeScreen,
                ObservationDetail,
            ),
        ):
            return False
        return True

    def on_key(self, event: events.Key) -> None:
        # Textual has no sequential chords; ``Binding("d,d")`` means ``d`` OR ``d``.
        if event.key != "d":
            if self._pending_d:
                self._clear_pending_d()
            return
        if not self._delete_action_allowed():
            self._clear_pending_d()
            return
        if self._pending_d:
            self._clear_pending_d()
            event.prevent_default()
            event.stop()
            self.action_delete_action()
            return
        self._pending_d = True
        self._d_timer = self.set_timer(self._DD_TIMEOUT, self._expire_pending_d)
        event.prevent_default()
        event.stop()

    def action_delete_action(self) -> None:
        """``dd`` — cascade-delete the active action (and children). No confirm."""
        if not self._delete_action_allowed():
            return
        assert self.active is not None
        assert self.investigation is not None
        action_id = self.active.action
        if action_id == ROOT_ACTION_ID:
            self.notify("Cannot delete the root action", severity="warning")
            return
        try:
            removed = remove_action(
                self.investigation,
                action_id,
                force=True,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._order = self.investigation.bfs_order()
        self.active = load_active(app_dir=self.app_dir)
        self.notify("Deleted " + ", ".join(str(i) for i in removed))
        self._show_current_action(reset=True)

    def cycle_workspace(self, delta: int) -> None:
        """Switch workspaces (workspace drawer). Reopens the drawer if it was top."""
        reopen = isinstance(self.screen, WorkspaceDrawer)
        if reopen:
            self.pop_screen()
        self._navigate_workspace(delta)
        if reopen:
            self.push_screen(WorkspaceDrawer(self.workspace, self.labels))

    def _ui_nav_frame(self) -> tuple[str, int] | None:
        if not self.investigation or not self.active:
            return None
        return (self.investigation.id, self.active.action)

    def _record_ui_nav(self) -> None:
        if self._ui_nav_silent:
            return
        frame = self._ui_nav_frame()
        if frame is None:
            return
        if self._ui_nav_back and self._ui_nav_back[-1] == frame:
            return
        self._ui_nav_back.append(frame)
        self._ui_nav_fwd.clear()

    def _jump_ui_nav(self, frame: tuple[str, int]) -> None:
        inv_id, action_id = frame
        self._ui_nav_silent = True
        try:
            if self.investigation is None or self.investigation.id != inv_id:
                order = list(self.project.investigations) if self.project else []
                if inv_id not in order:
                    self.notify(
                        f"Cannot restore tab {inv_id}", severity="warning"
                    )
                    return
                self._switch_investigation(
                    inv_id, action_id, workspace=DEFAULT_WORKSPACE_ID
                )
                return
            if action_id not in self.investigation.actions:
                self.notify("Previous view no longer exists", severity="warning")
                return
            self._leave_destination_for_action()
            assert self.active is not None
            self._write_active(self._make_active(action=action_id))
            self._show_current_action(reset=True)
        finally:
            self._ui_nav_silent = False

    def action_nav_back(self) -> None:
        """Ctrl-o — previous UI view (not the research action log)."""
        if not self._ui_nav_back:
            self.notify("No previous view", severity="warning")
            return
        current = self._ui_nav_frame()
        if current is not None:
            self._ui_nav_fwd.append(current)
        frame = self._ui_nav_back.pop()
        self._jump_ui_nav(frame)

    def action_nav_forward(self) -> None:
        """Ctrl-i — next UI view after a back."""
        if not self._ui_nav_fwd:
            self.notify("No forward view", severity="warning")
            return
        current = self._ui_nav_frame()
        if current is not None:
            self._ui_nav_back.append(current)
        frame = self._ui_nav_fwd.pop()
        self._jump_ui_nav(frame)

    def _activate_action(self, action_id: int) -> None:
        self._record_ui_nav()
        self._leave_destination_for_action()
        assert self.active is not None
        self._write_active(self._make_active(action=action_id))
        self._show_current_action(reset=True)

    def _navigate_sibling(self, delta: int) -> None:
        if self._invalid or not self.active or not self.investigation:
            self.notify("No actions to navigate", severity="warning")
            return
        sibs = self.investigation.sibling_ids(self.active.action)
        if not sibs:
            return
        try:
            idx = sibs.index(self.active.action)
        except ValueError:
            idx = 0
        new_idx = idx + delta
        if new_idx < 0:
            self.notify("First sibling", severity="warning")
            return
        if new_idx >= len(sibs):
            self.notify("Last sibling", severity="warning")
            return
        self._activate_action(sibs[new_idx])

    def _navigate_deeper(self) -> None:
        if self._invalid or not self.active or not self.investigation:
            return
        kids = self.investigation.children(self.active.action)
        if not kids:
            self.notify("No child actions", severity="warning")
            return
        self._activate_action(kids[0].id)

    def _navigate_shallower(self) -> None:
        if self._invalid or not self.active or not self.investigation:
            return
        action = self.investigation.get_action(self.active.action)
        if action.parent_id is None:
            self.notify("Already at root", severity="warning")
            return
        self._activate_action(action.parent_id)

    def _navigate_workspace(self, delta: int) -> None:
        if self._invalid or not self.active or not self._ws_order:
            self.notify("No workspaces to navigate", severity="warning")
            return
        self._persist_workspace_if_dirty()
        try:
            idx = self._ws_order.index(self.active.workspace)
        except ValueError:
            idx = 0
        new_idx = idx + delta
        if new_idx < 0:
            self.notify("First workspace", severity="warning")
            return
        if new_idx >= len(self._ws_order):
            self.notify("Last workspace", severity="warning")
            return
        new_ws = self._ws_order[new_idx]
        self._write_active(self._make_active(workspace=new_ws))
        assert self.investigation is not None
        self._apply_workspace(self.investigation.get_workspace(new_ws))
        self._show_current_action(reset=True)
        label = self.investigation.workspace.label or "workspace"
        self.notify(f"Workspace {label}", timeout=2)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {
            "go_home",
            "universal_search",
            "new_investigation_tab",
            "tab_jump",
            "save_active_query",
            "edit_active_query",
            "feedback_prompt",
            "bug_report",
            "show_activity",
        } and isinstance(self.focused, Input):
            return False
        tree_actions = {
            "tutorials",
            "command_palette",
            "next_sibling",
            "prev_sibling",
            "child_action",
            "parent_action",
            "next_investigation",
            "prev_investigation",
            "show_help",
        }
        if action in tree_actions:
            if isinstance(self.focused, Input):
                return False
            # Drawers own up/down (and their own chrome); don't steal arrows.
            if action in {
                "next_sibling",
                "prev_sibling",
                "child_action",
                "parent_action",
            } and isinstance(
                self.screen,
                (
                    WorkspaceDrawer,
                    SearchDrawer,
                    ActivityDrawer,
                    CommandPalette,
                    DrillDownModal,
                    DestinationScreen,
                    HomeScreen,
                    ObservationDetail,
                ),
            ):
                return False
        return True

    def action_go_home(self) -> None:
        """Dismiss overlays and open the active investigation's research home."""
        if self._invalid or not self.investigation or not self.active:
            return
        self._nav_dest = None
        self._nav_return = None
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if self.active.action == ROOT_ACTION_ID:
            self._show_front(reset=True)
            self.notify("Home", timeout=1)
            return
        self._activate_action(ROOT_ACTION_ID)

    def action_new_investigation_tab(self) -> None:
        """Shift+N / pane ``+`` — create a blank investigation in this project."""
        # Leave the Shift+T tab menu if it's open.
        try:
            from ..actions.screen import BaseScreen
            from .shell import PaneBar

            if isinstance(self.screen, BaseScreen):
                pane = self.screen.query_one("#shell-pane-bar", PaneBar)
                if pane._jumping:
                    pane.cancel_jump()
        except Exception:  # noqa: BLE001
            pass
        if self._invalid or not self.project_id:
            self.notify("No project loaded", severity="error")
            return
        from .commands import ParsedCommand

        self._cmd_tab_new(
            ParsedCommand(name="tab", options={"op": "new"}, args=[])
        )

    def action_tab_jump(self) -> None:
        """Shift+T — open the tab menu (digits / N new / X close)."""
        start = getattr(self.screen, "action_shell_tab_jump", None)
        if callable(start):
            start()

    def switch_investigation_tab(self, investigation_id: str) -> None:
        """Activate another investigation tab (click or numbered jump)."""
        if self._invalid or not self.active or not self.project:
            return
        if investigation_id == self.active.investigation:
            return
        if investigation_id not in self.project.investigations:
            self.notify(f"Unknown tab {investigation_id!r}", severity="error")
            return
        self._inv_resume[self.active.investigation] = (
            self.active.workspace,
            self.active.action,
        )
        workspace, action = self._inv_resume.get(
            investigation_id, (DEFAULT_WORKSPACE_ID, ROOT_ACTION_ID)
        )
        self._switch_investigation(
            investigation_id, action, workspace=workspace
        )
        label = self._inv_titles.get(investigation_id, investigation_id)
        self.notify(f"Investigation {label}", timeout=2)

    def close_investigation_tab(
        self, investigation_id: str | None = None, *, force: bool = True
    ) -> None:
        """Close a tab (active if omitted). Refuses the last remaining tab.

        ``force`` is accepted for call-site compat and ignored — closes are
        immediate (no confirm modal).
        """
        del force
        if self._invalid or not self.project_id or not self.active or not self.project:
            return
        inv_id = investigation_id or self.active.investigation
        if inv_id not in self.project.investigations:
            self.notify(f"Unknown tab {inv_id!r}", severity="error")
            return
        if len(self.project.investigations) <= 1:
            self.notify("Cannot close the only tab", severity="warning")
            return
        self._persist_workspace_if_dirty()
        try:
            remove_investigation_from_project(
                self.project_id,
                inv_id,
                app_dir=self.app_dir,
                activate_fallback=True,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._inv_resume.pop(inv_id, None)
        self._nav_dest = None
        self._reload_everything(force_screen=True)
        self.notify(f"Removed tab {inv_id}")

    def _preferred_query_id(self) -> int | None:
        if not self.investigation or not self.investigation.queries:
            return None
        # Prefer the single draft, else newest saved.
        draft = self._find_draft_query()
        if draft is not None:
            return draft.id
        queries = sorted(
            self.investigation.queries.values(),
            key=lambda q: -q.id,
        )
        return queries[0].id if queries else None

    def _find_draft_query(self):
        """Return the investigation's sole draft (``in_report=False``), if any."""
        if not self.investigation:
            return None
        drafts = [
            q for q in self.investigation.queries.values() if not q.in_report
        ]
        if not drafts:
            return None
        drafts.sort(key=lambda q: q.id)
        return drafts[-1]

    def _consolidate_drafts(self) -> None:
        """Enforce at most one draft — keep newest; save or drop the rest."""
        if not self.investigation:
            return
        drafts = sorted(
            (q for q in self.investigation.queries.values() if not q.in_report),
            key=lambda q: q.id,
        )
        if len(drafts) <= 1:
            return
        keep = drafts[-1]
        for orphan in drafts[:-1]:
            if orphan.is_empty():
                try:
                    remove_query(
                        self.investigation,
                        orphan.id,
                        app_dir=self.app_dir,
                        persist=False,
                    )
                except Exception:  # noqa: BLE001
                    orphan.in_report = True
            else:
                # Salvage content rather than delete research.
                orphan.in_report = True
        persist_investigation(self.investigation, app_dir=self.app_dir)
        _ = keep

    def ensure_draft_query(
        self,
        *,
        reset: bool = False,
        name: Optional[str] = None,
        metric: Optional[list[str]] = None,
        entity: Optional[list[str]] = None,
        series: Optional[list[str]] = None,
        frequency: Optional[str] = None,
        head: Optional[int] = None,
        tail: Optional[int] = None,
        from_workspace: bool = False,
    ):
        """Return the single draft query, creating or replacing contents as needed."""
        if not self.investigation:
            raise InvestigationError("No investigation loaded")
        self._consolidate_drafts()
        draft = self._find_draft_query()
        if draft is None:
            draft = add_query(
                self.investigation,
                name=name,
                metric=metric,
                entity=entity,
                series=series,
                frequency=frequency,
                head=head,
                tail=tail,
                from_workspace=from_workspace,
                app_dir=self.app_dir,
                persist=False,
            )
            draft.in_report = False
            persist_investigation(self.investigation, app_dir=self.app_dir)
            return draft

        replace = reset or from_workspace or any(
            x is not None for x in (metric, entity, series, name, frequency, head, tail)
        )
        if not replace:
            return draft

        if from_workspace:
            ws = self.workspace
            draft.metric = list(ws.metric)
            draft.entity = list(ws.entity)
            draft.series = list(ws.series)
            draft.frequency = ws.frequency
            draft.head = ws.head
            draft.tail = ws.tail
            draft.name = name
        elif reset and metric is None and entity is None and series is None:
            # Fresh empty draft slot (e.g. after save).
            draft.name = None
            draft.metric = []
            draft.entity = []
            draft.series = []
            draft.frequency = None
            draft.head = None
            draft.tail = None
        else:
            if metric is not None:
                draft.metric = list(metric)
            if entity is not None:
                draft.entity = list(entity)
            if series is not None:
                draft.series = list(series)
            elif reset:
                draft.series = []
            if frequency is not None:
                draft.frequency = frequency
            if head is not None:
                draft.head = head
            elif reset:
                draft.head = None
            if tail is not None:
                draft.tail = tail
            elif reset:
                draft.tail = None
            if name is not None:
                draft.name = name
        draft.in_report = False
        persist_investigation(self.investigation, app_dir=self.app_dir)
        return draft

    def refresh_shell_chrome(self, screen: Screen | None = None) -> None:
        """Push project/tabs/sidebar state into the front BaseScreen shell."""
        from ..actions.screen import BaseScreen
        from .shell import PaneBar, ProjectHeader, ResearchSidebar

        target = screen or self.screen
        if not isinstance(target, BaseScreen) or not target.SHELL_ENABLED:
            return
        try:
            header = target.query_one("#shell-header", ProjectHeader)
            pane_bar = target.query_one("#shell-pane-bar", PaneBar)
            sidebar = target.query_one("#shell-sidebar", ResearchSidebar)
        except Exception:  # noqa: BLE001
            return

        title = "JST"
        if self.investigation:
            title = self.investigation.title or self.investigation.id
        if self.project and self.project_id:
            title = self.project.title or self.project_id
        header.set_project(title)

        order = list(self.project.investigations) if self.project else []
        if not order and self.active:
            order = [self.active.investigation]
        marks: dict[str, str] = {}
        for inv_id, mark in getattr(self, "_tab_activity_marks", {}).items():
            marks[inv_id] = mark
        pane_bar.set_tabs(
            order,
            labels=self._inv_titles,
            active=self.active.investigation if self.active else None,
            marks=marks,
        )

        queries = []
        active_qid = None
        if self.investigation:
            self._consolidate_drafts()
            if self._find_draft_query() is None:
                try:
                    self.ensure_draft_query()
                except Exception:  # noqa: BLE001
                    pass
            for q in sorted(self.investigation.queries.values(), key=lambda x: x.id):
                queries.append(
                    {
                        "id": q.id,
                        "name": q.name or "",
                        "in_report": bool(q.in_report),
                        "empty": q.is_empty(),
                    }
                )
            try:
                action = self.investigation.get_action(self.active.action)
                if action.type == "query":
                    active_qid = action.args.get("query_id")
            except Exception:  # noqa: BLE001
                active_qid = None
        overview_active = bool(
            self.active and self.active.action == ROOT_ACTION_ID
        )
        sidebar.refresh_queries(
            queries,
            active_query_id=active_qid,
            overview_active=overview_active,
        )

        ws = self.workspace
        labels = self.labels

        def _rows(ids: list[str]) -> list[tuple[str, str]]:
            seen: set[str] = set()
            out: list[tuple[str, str]] = []
            for rid in ids:
                if rid in seen:
                    continue
                seen.add(rid)
                out.append((rid, labels.get(rid, rid)))
            return out

        active_entities: list[tuple[str, str]] = []
        active_metrics: list[tuple[str, str]] = []
        active_series: list[tuple[str, str]] = []
        missing_typed: list[tuple[str, str]] = []
        if active_qid is not None and self.investigation is not None:
            try:
                aq = self.investigation.get_query(int(active_qid))
            except Exception:  # noqa: BLE001
                aq = None
            if aq is not None:
                for rid in aq.metric:
                    if rid not in labels:
                        missing_typed.append(("metric", rid))
                for rid in aq.entity:
                    if rid not in labels:
                        missing_typed.append(("entity", rid))
                for rid in aq.series:
                    if rid not in labels:
                        missing_typed.append(("series", rid))
                active_entities = _rows(list(aq.entity))
                active_metrics = _rows(list(aq.metric))
                active_series = _rows(list(aq.series))

        sidebar.refresh_workspace(
            entities=_rows(list(ws.entity)),
            metrics=_rows(list(ws.metric)),
            series=_rows(list(ws.series)),
            active_entities=active_entities,
            active_metrics=active_metrics,
            active_series=active_series,
            active_query_id=int(active_qid) if active_qid is not None else None,
        )
        if missing_typed:
            self._hydrate_missing_labels(missing_typed)

    def action_universal_search(self) -> None:
        """Open universal search (global context)."""
        self._open_universal_search(context="global")

    def _open_universal_search(
        self,
        *,
        context: str = "global",
        resource: str = "all",
    ) -> None:
        from .universal_search import UniversalSearchModal

        def _done(result: object | None) -> None:
            if not result:
                return
            if isinstance(result, dict):
                action = result.get("action")
                if action == "inspect":
                    self.open_resource_view(
                        str(result.get("type")),
                        str(result.get("id")),
                        label=result.get("label"),
                    )
                elif action == "collect":
                    self._collect_resource(
                        str(result.get("type")), str(result.get("id"))
                    )
                elif action == "add_to_query":
                    rid = str(result.get("id"))
                    label = result.get("label")
                    if label:
                        self.remember_label(rid, str(label))
                    self._add_resource_to_active_query(
                        str(result.get("type")), rid
                    )

        selected_entities: list[str] = []
        selected_metrics: list[str] = []
        if self.investigation is not None and self.active is not None:
            try:
                action = self.investigation.get_action(self.active.action)
                if action.type == "query":
                    qid = action.args.get("query_id")
                    if qid is not None:
                        aq = self.investigation.get_query(int(qid))
                        selected_entities = list(aq.entity)
                        selected_metrics = list(aq.metric)
            except Exception:  # noqa: BLE001
                pass

        self.push_screen(
            UniversalSearchModal(
                self.client,
                context=context,
                resource=resource,
                app_dir=self.app_dir,
                project_id=self.project_id,
                investigation_id=(
                    self.active.investigation if self.active else None
                ),
                selected_entities=selected_entities,
                selected_metrics=selected_metrics,
            ),
            _done,
        )

    def _collect_resource(self, resource_type: str, resource_id: str) -> None:
        ws = self.workspace
        added = False
        if resource_type == "metric":
            added = ws.add_metric(resource_id)
        elif resource_type == "entity":
            added = ws.add_entity(resource_id)
        elif resource_type == "series":
            added = ws.add_series(resource_id)
        if added:
            self.persist_active_workspace()
            self.refresh_shell_chrome()
            self.notify(f"Collected {resource_id}")
        else:
            self.notify(f"{resource_id} already in workspace")

    def query_compose_add_resource(self, kind: str, query_id: int) -> None:
        """Sidebar ``+ Add`` under the active query — open scoped picker."""
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            self.investigation.get_query(int(query_id))
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        screen = getattr(self, "screen", None)
        if getattr(screen, "query_id", None) != int(query_id):
            self.open_query_view(int(query_id))
            screen = getattr(self, "screen", None)
        opener = getattr(screen, "_open_scoped_picker", None)
        if callable(opener):
            opener(kind)
        else:
            self.notify("Open the query to add resources", severity="warning")

    def query_compose_remove_resource(
        self, kind: str, resource_id: str, query_id: int
    ) -> None:
        """Sidebar chip remove under the active query composition."""
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        screen = getattr(self, "screen", None)
        if getattr(screen, "query_id", None) != int(query_id):
            self.open_query_view(int(query_id))
            screen = getattr(self, "screen", None)
        remover = getattr(screen, "_remove_query_resource", None)
        if callable(remover):
            remover(kind, resource_id)
            return
        try:
            query = self.investigation.get_query(int(query_id))
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        if kind == "entity" and resource_id in query.entity:
            query.entity = [x for x in query.entity if x != resource_id]
        elif kind == "metric" and resource_id in query.metric:
            query.metric = [x for x in query.metric if x != resource_id]
        elif kind == "series" and resource_id in query.series:
            query.series = [x for x in query.series if x != resource_id]
        else:
            return
        persist_investigation(self.investigation, app_dir=self.app_dir)
        self.refresh_shell_chrome()

    def _add_resource_to_active_query(
        self, resource_type: str, resource_id: str
    ) -> None:
        """Append a resource onto the active query draft and reopen."""
        if not self.investigation or not self.active:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            action = self.investigation.get_action(self.active.action)
        except InvestigationError:
            action = None
        qid = None
        if action and action.type == "query":
            qid = action.args.get("query_id")
        if qid is None:
            # Seed / replace the single draft around this resource.
            kwargs: dict = {"reset": True}
            if resource_type == "metric":
                kwargs["metric"] = [resource_id]
            elif resource_type == "entity":
                kwargs["entity"] = [resource_id]
            elif resource_type == "series":
                kwargs["series"] = [resource_id]
            else:
                self.notify(f"Cannot add {resource_type} to query", severity="warning")
                return
            try:
                query = self.ensure_draft_query(**kwargs)
            except InvestigationError as exc:
                self.notify(str(exc), severity="error")
                return
            self.open_query_view(query.id)
            self.refresh_shell_chrome()
            return
        query = self.investigation.get_query(int(qid))
        if resource_type == "metric" and resource_id not in query.metric:
            query.metric.append(resource_id)
        elif resource_type == "entity" and resource_id not in query.entity:
            query.entity.append(resource_id)
        elif resource_type == "series" and resource_id not in query.series:
            query.series.append(resource_id)
        else:
            self.notify(f"{resource_id} already on query {qid}")
            return
        persist_investigation(self.investigation, app_dir=self.app_dir)
        self.notify(f"Added {resource_id} to query {qid}")
        self.open_query_view(int(qid))
        self.refresh_shell_chrome()

    def action_new_draft_query(self) -> None:
        """Reset the single draft (from workspace) or search to seed one."""
        if self._invalid or not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        if self.workspace.is_empty():
            self.notify("Search for a metric or entity to start a query")
            self._open_universal_search(context="new_query", resource="all")
            return
        try:
            query = self.ensure_draft_query(reset=True, from_workspace=True)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Draft query {query.id}")
        self.open_query_view(query.id)
        self.refresh_shell_chrome()

    def action_save_active_query(self) -> None:
        """Promote the active query to saved (``s``); mint a fresh empty draft."""
        if not self.investigation or not self.active:
            return
        try:
            action = self.investigation.get_action(self.active.action)
        except InvestigationError:
            self.notify("No active query", severity="warning")
            return
        if action.type != "query":
            self.notify("Open a query to save it", severity="warning")
            return
        qid = action.args.get("query_id")
        if qid is None:
            return
        query = self.investigation.get_query(int(qid))
        if query.is_empty():
            self.notify("Nothing to save — draft is empty", severity="warning")
            return
        if not query.name:
            from .naming import suggest_query_name

            query.name = suggest_query_name(
                query.metric, query.entity, labels=self.labels
            )
        query.in_report = True
        persist_investigation(self.investigation, app_dir=self.app_dir)
        # Log via promote_query for audit trail.
        try:
            from ..ops.curation import promote_query

            promote_query(
                int(qid),
                project=self.project_id,
                investigation=self.active.investigation,
                app_dir=self.app_dir,
            )
        except Exception:  # noqa: BLE001
            pass
        # Always keep one empty draft slot after save.
        try:
            self.ensure_draft_query(reset=True)
        except Exception:  # noqa: BLE001
            pass
        self.notify(f"Saved query {qid}")
        self.refresh_shell_chrome()

    def action_edit_active_query(self) -> None:
        """Composition lives on the query screen header — open the query."""
        if not self.investigation or not self.active:
            return
        try:
            action = self.investigation.get_action(self.active.action)
        except InvestigationError:
            self.notify("No active query", severity="warning")
            return
        if action.type != "query":
            self.notify("Open a query to compose it", severity="warning")
            return
        qid = action.args.get("query_id")
        if qid is None:
            return
        self.open_query_view(int(qid))
        self.notify("Edit composition in the header · E/M/S to add · r to refresh")

    def action_feedback_prompt(self) -> None:
        """Prompt for feedback text then queue the local outbox."""
        from .feedback_modal import FeedbackModal

        def _done(message: str | None) -> None:
            if message:
                self._cmd_feedback(message)

        self.push_screen(FeedbackModal(), _done)

    def action_bug_report(self) -> None:
        """``!`` — expand footer bug form (keeps the screen context visible)."""
        screen = self.screen
        # Toggle closed if already open on this screen.
        cancel = getattr(screen, "cancel_bug_report", None)
        if callable(cancel):
            try:
                panel_open = getattr(
                    screen.query_one("#bug-report-panel"), "is_open", False
                )
            except Exception:  # noqa: BLE001
                panel_open = False
            if panel_open:
                cancel()
                return

        summary = ""
        if hasattr(screen, "bug_report_summary"):
            try:
                summary = str(screen.bug_report_summary() or "")
            except Exception:  # noqa: BLE001
                summary = f"[dim]{type(screen).__name__}[/]"
        else:
            summary = f"[dim]{type(screen).__name__}[/]"
        # Snapshot at ``!`` time — before the user edits the form.
        replication = self._screen_bug_replication(screen)
        screen_name = type(screen).__name__

        open_panel = getattr(screen, "open_bug_report", None)
        if callable(open_panel):
            open_panel(
                context_summary=summary,
                replication=replication,
                screen_name=screen_name,
            )
            return

        # Non-shell screens: fall back to a quick prompt via notify + empty save.
        self.notify(
            "Bug report form needs a research screen — open a query/view first",
            severity="warning",
        )

    def _screen_bug_replication(self, screen=None) -> dict:
        screen = screen if screen is not None else self.screen
        if hasattr(screen, "bug_report_snapshot"):
            try:
                snap = screen.bug_report_snapshot()
                if isinstance(snap, dict):
                    return snap
            except Exception as exc:  # noqa: BLE001
                return {
                    "kind": "error",
                    "screen": type(screen).__name__,
                    "error": str(exc),
                }
        return {"kind": "screen", "screen": type(screen).__name__}

    def _save_bug_report(
        self,
        message: str,
        *,
        replication: dict,
        screen_name: str,
    ) -> None:
        from ..ops import bug_report as br

        payload = br.save_bug_report(
            message,
            replication=replication,
            screen=screen_name,
            project_id=self.project_id,
            investigation_id=(
                self.active.investigation if self.active else None
            ),
            app_dir=self.app_dir,
        )
        path = payload.get("path") or ""
        rid = payload.get("bug_report_id") or ""
        self.notify(f"Bug report saved ({rid}) → {path}", timeout=6)

    def ensure_action(
        self,
        action_type: str,
        *,
        args: Optional[dict] = None,
        description: Optional[str] = None,
        match: str = "type",
    ):
        """Activate an existing root child of this type, or add one."""
        if self._invalid or not self.investigation or not self.active:
            self.notify("No investigation loaded", severity="error")
            return None
        args = dict(args or {})
        for child in self.investigation.children(ROOT_ACTION_ID):
            if child.type != action_type:
                continue
            if match == "args" and dict(child.args) != args:
                continue
            self._activate_action(child.id)
            return child
        self._record_ui_nav()
        try:
            action = add_action(
                self.investigation,
                action_type,
                parent_id=ROOT_ACTION_ID,
                args=args,
                description=description,
                activate=True,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return None
        self._order = self.investigation.bfs_order()
        self.active = load_active(app_dir=self.app_dir)
        self.notify(f"Opened {action.type} {action.id}")
        self._show_current_action(reset=True)
        return action

    def open_query_view(self, query_id: int) -> None:
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            query = self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.ensure_action(
            "query",
            args={"query_id": query_id},
            description=query.name or f"Query {query_id}",
            match="args",
        )

    def open_direct_query_chart(
        self,
        *,
        metric_ids: list[str],
        entity_ids: list[str],
        name: str | None = None,
    ) -> None:
        """Create a draft query from metric×entity and open the chart view."""
        if self._invalid or not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        if not metric_ids or not entity_ids:
            self.notify("Need metric and entity for a chart", severity="warning")
            return
        try:
            from .naming import suggest_query_name

            auto_name = name or suggest_query_name(
                metric_ids, entity_ids, labels=self.labels
            )
            query = self.ensure_draft_query(
                reset=True,
                name=auto_name,
                metric=list(metric_ids),
                entity=list(entity_ids),
                tail=20,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.open_query_view(query.id)
        self.refresh_shell_chrome()

    def query_from_workspace(self) -> None:
        """Snapshot the workspace bag into the single draft and open it."""
        if self._invalid or not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        if self.workspace.is_empty():
            self.notify(
                "Workspace is empty — Search and collect ids first",
                severity="warning",
            )
            return
        try:
            query = self.ensure_draft_query(reset=True, from_workspace=True)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Draft query {query.id}")
        self.open_query_view(query.id)
        self.refresh_shell_chrome()

    def action_command_palette(self) -> None:
        if self._invalid:
            return
        self.push_screen(
            CommandPalette(
                history=self._command_history,
                investigation=self.investigation,
            ),
            self._on_command_submitted,
        )

    def _on_command_submitted(self, line: str | None) -> None:
        if not line:
            return
        if line not in self._command_history:
            self._command_history.append(line)
        goto = parse_goto(line)
        if goto is not None:
            self._pending_goto = goto
            self.call_later(self._run_pending_command)
            return
        try:
            parsed = parse_command_line(line)
        except CommandError as exc:
            self.notify(str(exc), severity="error")
            return
        # Run after the palette finishes dismissing — screen switches inside
        # the dismiss callback can wedge Textual's message pump.
        self._pending_command = parsed
        self.call_later(self._run_pending_command)

    def _run_pending_command(self) -> None:
        goto = self._pending_goto
        self._pending_goto = None
        if goto is not None:
            if goto == "home":
                self.action_go_home()
            else:
                self.open_destination(goto)
            return
        parsed = self._pending_command
        self._pending_command = None
        if parsed is not None:
            self._dispatch_command(parsed)

    def _dispatch_command(self, parsed: ParsedCommand) -> None:
        if parsed.name == "workspace":
            self._open_workspace_drawer()
        elif parsed.name == "search":
            self._open_search_drawer(parsed.args[0], parsed.args[1])
        elif parsed.name == "query":
            qid = parsed.options.get("query_id")
            if qid is None:
                self.query_from_workspace()
            else:
                self.open_query_view(int(qid))
        elif parsed.name == "add":
            self._cmd_add(parsed)
        elif parsed.name == "rm":
            self._cmd_rm(parsed, force=bool(parsed.options.get("force")))
        elif parsed.name == "rename":
            self._cmd_rename(parsed)
        elif parsed.name == "tab":
            self._cmd_tab(parsed)
        elif parsed.name == "annotate":
            self._cmd_annotate(parsed)
        elif parsed.name == "publish":
            self._cmd_publish()
        elif parsed.name == "tutorial":
            self._open_tutorial_menu_screen(False)
        elif parsed.name == "tips":
            state = parsed.options.get("state")
            self.set_tips(not self._tips_on if state is None else bool(state))
        elif parsed.name == "feedback":
            self._cmd_feedback(str(parsed.options.get("message") or ""))
        elif parsed.name == "activity":
            self.action_show_activity()

    def _feedback_diagnostics(self) -> dict:
        from ..ops import feedback as fb

        inv = self.investigation
        ws = inv.workspace if inv else None
        action = None
        if inv and self.active and self.active.action in inv.actions:
            action = inv.actions[self.active.action]
        return fb.capture_diagnostics(
            screen=type(self.screen).__name__,
            project_id=self.project_id,
            investigation_id=(
                self.active.investigation if self.active else None
            ),
            workspace=(
                {
                    "metric": list(ws.metric),
                    "entity": list(ws.entity),
                    "series": list(ws.series),
                    "taxonomy": ws.taxonomy,
                    "head": ws.head,
                    "tail": ws.tail,
                }
                if ws
                else None
            ),
            view={
                "type": type(self.screen).__name__,
                "action_id": self.active.action if self.active else None,
                "action_type": action.type if action else None,
                "args": dict(action.args) if action else None,
                "size": (
                    [self.size.width, self.size.height]
                    if getattr(self, "size", None)
                    else None
                ),
            },
            app_dir=self.app_dir,
            extra={
                "action": self.active.action if self.active else None,
            },
        )

    def _cmd_feedback(self, message: str) -> None:
        from ..ops import feedback as fb

        result = fb.submit_feedback(
            message,
            project_id=self.project_id,
            investigation_id=(
                self.active.investigation if self.active else None
            ),
            screen=type(self.screen).__name__,
            diagnostics=self._feedback_diagnostics(),
            app_dir=self.app_dir,
        )
        if result.status == "error":
            self.notify(result.error or "Feedback failed", severity="error")
            return
        fid = (result.data or {}).get("feedback_id")
        self.notify(
            f"Feedback queued locally ({fid}). "
            "Inspect with `jst actions feedback ls`."
        )

    def _cmd_publish(self) -> None:
        """Draft the page's text, confirm, then publish in a background thread."""
        inv = self.investigation
        if not inv:
            self.notify("No investigation loaded", severity="error")
            return
        if self._publishing:
            self.notify("Already publishing…")
            return
        # Multi-tab remote publish awaits Workstream C — never silently flatten.
        if self.project and len(self.project.investigations) > 1:
            self.notify(
                "This project has multiple investigation tabs. "
                "Remote multi-tab publish awaits Workstream C. "
                "Use `jst preview` for a local multi-tab preview, "
                "or publish a single-tab project for now.",
                severity="warning",
                timeout=12,
            )
            return
        report_queries = [q for q in inv.queries.values() if q.in_report]
        if not report_queries:
            self.notify(
                "Nothing to publish yet. Save a report query with :query, "
                "or promote one with `jst actions promote`.",
                severity="warning",
            )
            return
        try:
            api_key = self.client.api_key
        except ApiKeyNotSetError:
            self.notify(
                "Publishing needs an API key. Run `jst login` first.",
                severity="error",
            )
            return
        self._open_publish_draft(api_key)

    def _open_publish_draft(
        self,
        api_key: str,
        text: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """The whole page as one Markdown draft; a retry keeps the author's text."""
        from ..publish import PublishError, last_publication
        from ..publish_draft import build_draft
        from ..publish_sync import label, pull_for_publish

        inv = self.investigation
        if not inv:
            return
        web_versions = None
        previous = last_publication(inv.id, app_dir=self.app_dir)
        if text is None and previous is not None:
            # Pull text edited on the page since the last publish (JEF-166).
            try:
                pull = pull_for_publish(inv, previous)
            except PublishError as exc:
                self.notify(
                    escape(f"Couldn't check the page for edits: {exc}"),
                    severity="warning",
                )
            else:
                if pull.pulled:
                    self._save_investigation(pull.investigation)
                    inv = pull.investigation
                    self.notify(
                        "Pulled edits made on the page: "
                        + ", ".join(label(k) for k in pull.pulled)
                    )
                if pull.conflicts:
                    web_versions = pull.conflicts
                    self.notify(
                        "Edited both here and on the page: "
                        + ", ".join(label(k) for k in pull.conflicts)
                        + ". The page's version is in the draft.",
                        severity="warning",
                        timeout=10,
                    )
        self.push_screen(
            PublishDraftScreen(
                text if text is not None else build_draft(inv, web_versions),
                title=inv.title,
                error=error,
            ),
            lambda result: self._on_publish_draft(api_key, result),
        )

    def _save_investigation(self, updated: Investigation) -> None:
        """Persist ``updated`` as the current investigation and refresh views."""
        self._suppress_poll = True
        try:
            persist_investigation(
                updated, app_dir=self.app_dir, project_id=self.project_id
            )
            self.investigation = updated
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False
        self._refresh_annotation_surfaces()
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_live()

    def _on_publish_draft(self, api_key: str, text: Optional[str]) -> None:
        from ..publish_draft import DraftError, apply_draft

        if text is None or not self.investigation:
            return
        try:
            updated, changes = apply_draft(self.investigation, text)
        except DraftError as exc:
            self._open_publish_draft(api_key, text=text, error=str(exc))
            return
        if changes:
            # Save the text to the investigation first, so it's kept even if
            # the user cancels the publish or it fails.
            self._save_investigation(updated)
            self.notify(f"Saved: {changes.summary()}")
        from ..publish import last_publication

        # Publish exactly what the dialog shows, even if the file reloads
        # (or another investigation opens) while it's open.
        snapshot = copy.deepcopy(self.investigation)
        previous = last_publication(snapshot.id, app_dir=self.app_dir)
        self.push_screen(
            PublishConfirmModal(
                snapshot, previous["url"] if previous is not None else None
            ),
            lambda mode: self._start_publish(
                snapshot,
                api_key,
                mode,
                previous["id"] if previous is not None else None,
            )
            if mode is not None
            else None,
        )

    def _start_publish(
        self,
        snapshot: Investigation,
        api_key: str,
        mode: str = "new",
        previous_id: Optional[str] = None,
    ) -> None:
        from ..publish import configured_site_url

        if not self.investigation or self.investigation.id != snapshot.id:
            self.notify(
                "The investigation changed while the dialog was open. "
                "Run :publish again.",
                severity="warning",
            )
            return
        site_url = configured_site_url(self.client)
        self._publishing = True
        n = len(snapshot.queries)
        self.notify(
            "Updating the page's text…"
            if mode == "update"
            else f"Publishing: running {n} quer{'y' if n == 1 else 'ies'}…",
            timeout=10,
        )
        self.publish_outcome = None
        loop = asyncio.get_running_loop()
        # Textual's context (the active app), for the callback from the thread.
        context = contextvars.copy_context()
        # Not run_worker(thread=True): Textual waits for those threads when
        # the app exits, so `q` hung until the request finished (JEF-170).
        self.publish_thread = threading.Thread(
            target=self._publish_in_thread,
            args=(
                snapshot, api_key, site_url, mode, previous_id, loop, context
            ),
            name="jst-publish",
            daemon=True,
        )
        self.publish_thread.start()

    def _publish_in_thread(
        self,
        inv: Investigation,
        api_key: str,
        site_url: str,
        mode: str = "new",
        previous_id: Optional[str] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        context: Optional[contextvars.Context] = None,
    ) -> None:
        from ..publish import (
            PublishError,
            QueriesChangedError,
            publish_investigation,
            record_publication,
            update_publication,
        )
        from ..publish_sync import text_of

        url: Optional[str] = None
        error: Optional[str] = None
        try:
            if mode == "update" and previous_id is not None:
                result = update_publication(
                    inv, previous_id, api_key=api_key, site_url=site_url
                )
            else:
                result = publish_investigation(inv, api_key=api_key, site_url=site_url)
            url = result["url"]
            record_publication(
                inv.id, result, text=text_of(inv), app_dir=self.app_dir
            )
        except QueriesChangedError as exc:
            error = f"{exc} Run :publish again and press p for a new link."
        except PublishError as exc:
            error = str(exc)
        except Exception as exc:  # unexpected; keep the TUI alive
            error = f"Publish failed: {exc}"
        # Kept until the TUI shows it; if the TUI has closed by now,
        # run_investigation_host prints it instead.
        self.publish_outcome = PublishOutcome(url, error, mode == "update")
        if self._quitting or loop is None:
            return
        try:
            # Doesn't wait for the TUI (call_from_thread would, and could
            # block forever while the app is shutting down).
            loop.call_soon_threadsafe(
                self._show_publish_outcome, context=context
            )
        except RuntimeError:
            pass  # the event loop has closed

    def _show_publish_outcome(self) -> None:
        outcome = self.publish_outcome
        if outcome is None or self._quitting:
            return
        self.publish_outcome = None
        self._on_publish_done(outcome.url, outcome.error, outcome.updated)

    async def action_quit(self) -> None:
        """Quit; while publishing, only on a second `q` (JEF-170)."""
        if self._publishing:
            now = time.monotonic()
            armed = self._quit_armed_at
            if armed is None or now - armed > self._QUIT_AGAIN_TIMEOUT:
                self._quit_armed_at = now
                self.notify(
                    "Publishing… press q again to quit anyway. "
                    "The link will be printed in the terminal.",
                    severity="warning",
                    timeout=self._QUIT_AGAIN_TIMEOUT,
                )
                return
        self._quitting = True
        self.exit()

    def _on_publish_done(
        self, url: Optional[str], error: Optional[str], updated: bool = False
    ) -> None:
        self._publishing = False
        if error is not None:
            # Server messages can echo query names: show them as plain text.
            self.notify(escape(error), severity="error", timeout=10)
            return
        assert url is not None
        self.push_screen(PublishedModal(url, updated=updated))

    def _cmd_annotate(self, parsed: ParsedCommand) -> None:
        qid = parsed.options.get("query_id")
        if qid is None:
            self.edit_investigation_annotations()
        else:
            self.edit_query_annotations(int(qid))

    def _cmd_rename(self, parsed: ParsedCommand) -> None:
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        title = parsed.options.get("title")
        description = parsed.options.get("description")
        if title is None and description is None:
            # Interactive rename → inline edit on the research home.
            self.edit_investigation_description(focus="title")
            return
        self._apply_rename(title=title, notes=description)

    def _cmd_tab(self, parsed: ParsedCommand) -> None:
        """Investigation-tab lifecycle: new / dup / rm / next / prev / left / right."""
        if self._invalid or not self.project_id or not self.active:
            self.notify("No project loaded", severity="error")
            return
        op = str(parsed.options.get("op") or "next")
        if op == "next":
            self._cycle_investigation(1)
            return
        if op == "prev":
            self._cycle_investigation(-1)
            return
        if op == "left":
            self._cmd_tab_move(-1)
            return
        if op == "right":
            self._cmd_tab_move(1)
            return
        if op == "new":
            self._cmd_tab_new(parsed)
            return
        if op == "dup":
            self._cmd_tab_dup(parsed)
            return
        if op == "rm":
            self._cmd_tab_rm(force=bool(parsed.options.get("force")))
            return
        self.notify(f"Unknown tab op {op!r}", severity="error")

    def _cmd_tab_move(self, delta: int) -> None:
        assert self.project_id is not None and self.active is not None
        try:
            project = move_investigation(
                self.project_id,
                self.active.investigation,
                delta=delta,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.project = project
        self._refresh_inv_titles()
        for screen in self.screen_stack:
            refresh = getattr(screen, "refresh_footer", None)
            if callable(refresh):
                refresh()
        self.notify("Tab order updated", timeout=2)

    def _cmd_tab_new(self, parsed: ParsedCommand) -> None:
        assert self.project_id is not None
        title = parsed.options.get("title")
        inv_id = parsed.options.get("investigation_id")
        self._persist_workspace_if_dirty()
        try:
            inv = create_investigation(
                inv_id,
                title=title,
                project_id=self.project_id,
                activate=True,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._nav_dest = None
        self._reload_everything(force_screen=True)
        self.notify(f"Created tab {inv.title or inv.id}")

    def _cmd_tab_dup(self, parsed: ParsedCommand) -> None:
        assert self.project_id is not None and self.active is not None
        title = parsed.options.get("title")
        inv_id = parsed.options.get("investigation_id")
        self._persist_workspace_if_dirty()
        try:
            inv = duplicate_investigation(
                self.project_id,
                self.active.investigation,
                investigation_id=inv_id,
                title=title,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._switch_investigation(inv.id, ROOT_ACTION_ID)
        self.notify(f"Duplicated → {inv.title or inv.id}")

    def _cmd_tab_rm(self, *, force: bool) -> None:
        # Closes are immediate — ``force`` kept for command-parser compat.
        del force
        self.close_investigation_tab()

    def _apply_rename(
        self, *, title: Optional[str], notes: Optional[str]
    ) -> None:
        if not self.investigation:
            return
        self._suppress_poll = True
        try:
            try:
                update_investigation(
                    self.investigation,
                    title=title,
                    notes=notes,
                    app_dir=self.app_dir,
                )
            except InvestigationError as exc:
                self.notify(str(exc), severity="error")
                return
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False
        self._refresh_inv_titles()
        self.refresh_shell_chrome()
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_live()
        bits = []
        if title is not None:
            bits.append(f"title {title!r}")
        if notes is not None:
            bits.append(
                "description updated" if notes.strip() else "description cleared"
            )
        # Refresh research-home chrome if we're sitting on it.
        refresh = getattr(self.screen, "refresh_home", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001
                pass
        self.notify("Renamed: " + ", ".join(bits) if bits else "Saved")

    def edit_investigation_description(self, *, focus: str = "title") -> None:
        """Rename via the shell header; description still edits on research home."""
        if not self.investigation or not self.active:
            self.notify("No investigation loaded", severity="error")
            return
        if focus not in ("title", "description"):
            focus = "title"
        if focus == "title":
            try:
                from .shell import ProjectHeader

                header = self.screen.query_one("#shell-header", ProjectHeader)
                header.begin_edit()
                return
            except Exception:  # noqa: BLE001
                pass
        begin = getattr(self.screen, "begin_inline_edit", None)
        if callable(begin):
            begin(focus=focus)
            return
        # Navigate to research home, then start inline edit on mount.
        self._pending_inline_edit = focus
        self._nav_dest = None
        self._nav_return = None
        self._pop_transient_overlays()
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if self.active.action != ROOT_ACTION_ID:
            self._activate_action(ROOT_ACTION_ID)
        else:
            self._show_front(reset=True)
            self.call_later(self._consume_pending_inline_edit)

    def _consume_pending_inline_edit(self) -> None:
        focus = getattr(self, "_pending_inline_edit", None)
        if focus not in ("title", "description"):
            return
        self._pending_inline_edit = None
        begin = getattr(self.screen, "begin_inline_edit", None)
        if callable(begin):
            begin(focus=focus)

    def open_query_context_menu(self, query_id: int, *, label: str = "") -> None:
        """Sidebar ··· — rename or delete (Shift+X still deletes immediately)."""
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            query = self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        from .naming import suggest_query_name

        display = (
            label
            or query.name
            or suggest_query_name(
                query.metric, query.entity, labels=self.labels
            )
        )
        self.push_screen(
            QueryContextMenu(query_id=int(query_id), label=display),
            lambda result, qid=int(query_id): self._on_query_context_menu(
                qid, result
            ),
        )

    def _on_query_context_menu(
        self, query_id: int, result: str | None
    ) -> None:
        if result == "rename":
            self.edit_query_name(query_id)
        elif result == "delete":
            delete = getattr(self, "delete_query", None)
            if callable(delete):
                delete(query_id, confirmed=True)

    def edit_query_name(self, query_id: int) -> None:
        """Open a modal to rename a saved query."""
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            query = self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        from .naming import suggest_query_name

        current = query.name or suggest_query_name(
            query.metric, query.entity, labels=self.labels
        )
        self.push_screen(
            QueryRenameModal(
                title=f"RENAME QUERY · Q{query.id}",
                name=current,
            ),
            lambda result, qid=query_id: self._on_query_rename_submitted(
                qid, result
            ),
        )

    def _on_query_rename_submitted(
        self, query_id: int, result: str | None
    ) -> None:
        if result is None or not self.investigation:
            return
        self._suppress_poll = True
        try:
            try:
                update_query(
                    self.investigation,
                    query_id,
                    name=result,
                    app_dir=self.app_dir,
                )
            except InvestigationError as exc:
                self.notify(str(exc), severity="error")
                return
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False
        self.refresh_shell_chrome()
        paint = getattr(self.screen, "_paint_compose", None)
        if callable(paint):
            try:
                paint()
            except Exception:  # noqa: BLE001
                pass
        label = result.strip() or f"Q{query_id}"
        self.notify(f"Query renamed to {label}")

    def edit_investigation_annotations(self) -> None:
        """Open the annotation editor for the investigation."""
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        self.push_screen(
            AnnotationModal(
                title="INVESTIGATION ANNOTATIONS",
                text=self.investigation.annotations or "",
            ),
            self._on_investigation_annotations_submitted,
        )

    def _on_investigation_annotations_submitted(self, result: str | None) -> None:
        if result is None or not self.investigation:
            return
        self._suppress_poll = True
        try:
            update_investigation(
                self.investigation,
                annotations=result,
                app_dir=self.app_dir,
            )
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False
        self._refresh_annotation_surfaces()
        cleared = not result.strip()
        self.notify(
            "Investigation annotations cleared"
            if cleared
            else "Investigation annotations saved"
        )

    def edit_query_annotations(
        self,
        query_id: int,
        *,
        series_choices: Sequence[str] | None = None,
    ) -> None:
        """Open the query view and start an inline remark draft."""
        del series_choices  # Inline form cites the live chart cross-section.
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.open_query_view(int(query_id))

        def _start() -> None:
            screen = self.screen
            begin = getattr(screen, "begin_add_remark", None)
            if callable(begin):
                begin()

        self.call_later(_start)

    def _refresh_annotation_surfaces(self) -> None:
        """Repaint Home / query browsers after annotation edits."""
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_live()
            refresh = getattr(screen, "refresh_annotations", None)
            if callable(refresh):
                refresh()
            home = getattr(screen, "refresh_home", None)
            if callable(home):
                try:
                    home()
                except Exception:  # noqa: BLE001
                    pass

    def action_show_activity(self) -> None:
        """Open the action-log history drawer (Shift+L)."""
        from .artifact_modal import ArtifactModal

        def _opened(result: object | None) -> None:
            if not result:
                return
            if isinstance(result, str):
                event_id, mode = result, "inspect"
            elif isinstance(result, dict):
                event_id = result.get("event_id")
                mode = result.get("mode") or "inspect"
            else:
                return
            if not event_id:
                return
            if mode == "rerun":
                try:
                    from ..ops.rerun import rerun_event

                    out = rerun_event(
                        str(event_id),
                        refresh=True,
                        client=self.client,
                        app_dir=self.app_dir,
                    )
                    if out.status == "error":
                        self.notify(out.error or "Rerun failed", severity="error")
                    else:
                        self.notify(f"Reran → {out.event_id}")
                        self.refresh_shell_chrome()
                except Exception as exc:  # noqa: BLE001
                    self.notify(str(exc), severity="error")
                return
            self.push_screen(
                ArtifactModal(str(event_id), app_dir=self.app_dir)
            )

        self.push_screen(
            ActivityDrawer(
                project_id=self.project_id,
                investigation_id=(
                    self.active.investigation if self.active else None
                ),
                app_dir=self.app_dir,
            ),
            _opened,
        )

    def _open_workspace_drawer(self) -> None:
        # If already open, focus it by pushing a fresh one on top (dismiss old?).
        # Pushing another is fine; Esc closes topmost.
        self.push_screen(WorkspaceDrawer(self.workspace, self.labels))

    def _open_search_drawer(self, resource_type: str, query: str) -> None:
        self.push_screen(
            SearchDrawer(
                self.client,
                self.workspace,
                resource_type=resource_type,  # type: ignore[arg-type]
                query=query,
            )
        )

    def _cmd_add(self, parsed: ParsedCommand) -> None:
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        action_type = parsed.args[0]
        self._suppress_poll = True
        try:
            try:
                action = add_action(
                    self.investigation,
                    action_type,
                    parent_id=parsed.options.get("parent"),
                    args=parsed.options.get("args") or {},
                    activate=bool(parsed.options.get("activate", True)),
                    app_dir=self.app_dir,
                )
            except InvestigationError as exc:
                self.notify(str(exc), severity="error")
                return
            self._order = self.investigation.bfs_order()
            self.active = load_active(app_dir=self.app_dir)
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
                self._active_mtime = self._active_file().stat().st_mtime
                self._own_active_mtime = self._active_mtime
            except OSError:
                pass
            self.notify(
                f"Added action {action.id} ({action.type})"
                + (" — activated" if parsed.options.get("activate", True) else "")
            )
            if parsed.options.get("activate", True):
                self._leave_destination_for_action()
                self._show_current_action(reset=True)
        finally:
            self._suppress_poll = False

    def _cmd_rm(self, parsed: ParsedCommand, *, force: bool) -> None:
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        action_id = int(parsed.options["action_id"])
        try:
            removed = remove_action(
                self.investigation,
                action_id,
                force=force,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            msg = str(exc)
            if not force and "--force" in msg:
                self.push_screen(
                    ConfirmForceModal(msg),
                    lambda ok: self._cmd_rm(parsed, force=True) if ok else None,
                )
                return
            self.notify(msg, severity="error")
            return
        self._order = self.investigation.bfs_order()
        self.active = load_active(app_dir=self.app_dir)
        self.notify("Removed " + ", ".join(str(i) for i in removed))
        self._show_current_action(reset=True)

    def action_back(self) -> None:
        # Collapse footer bug form before other Esc behavior.
        cancel_bug = getattr(self.screen, "cancel_bug_report", None)
        if callable(cancel_bug) and cancel_bug():
            return
        # Cancel sidebar jump mode (Q/w → digit pick) before other Esc behavior.
        try:
            from .shell import ResearchSidebar

            sidebar = self.screen.query_one("#shell-sidebar", ResearchSidebar)
            if sidebar.cancel_jump():
                return
        except Exception:  # noqa: BLE001
            pass
        focused = self.focused
        screen = self.screen
        if getattr(focused, "id", None) in (
            "metrics-filter",
            "search-input",
            "rank-filter",
            "assoc-filter",
        ) and hasattr(screen, "action_leave_filter"):
            screen.action_leave_filter()
            return
        if focused and getattr(focused, "id", None) in (
            "inspector-search-input",
            "inspector-results-list",
        ):
            try:
                screen.query_one("#search-input", Input).focus()
            except Exception:
                pass
            return
        if isinstance(self.screen, HomeScreen) and self._nav_dest:
            self.leave_destination()
            return
        if self._nav_dest:
            self._nav_dest = None
            self._nav_return = None
            if self.active and self.active.action != ROOT_ACTION_ID:
                self._activate_action(ROOT_ACTION_ID)
            else:
                self._show_front(reset=True)
            return
        if self._nav_return:
            dest = self._nav_return
            self.open_destination(dest)
            return
        if self.active and self.active.action != ROOT_ACTION_ID:
            self.action_go_home()

    def action_show_shortcuts_hint(self) -> None:
        """``?`` — shortcuts live next to on-screen controls, not a help dump."""
        self.notify(
            "Shortcuts appear beside their controls "
            "(Q/w jump · ! bug report · E/M/S add · r refresh · / search)",
            timeout=5,
        )

    def action_show_help(self) -> None:
        # Retained for older callers; prefer on-screen shortcut hints.
        self.action_show_shortcuts_hint()

    def delete_query(self, query_id: int, *, confirmed: bool = True) -> None:
        """Delete a query immediately (no confirm / arm step)."""
        del confirmed
        if not self.investigation:
            return
        try:
            self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._confirm_delete_query(int(query_id))

    def _confirm_delete_query(self, query_id: int) -> None:
        if not self.investigation:
            return
        try:
            remove_query(self.investigation, query_id, app_dir=self.app_dir)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.active = load_active(app_dir=self.app_dir)
        self._order = self.investigation.bfs_order()
        self.notify(f"Deleted query {query_id}")
        self._show_front(reset=True)

    def _shell_sidebar(self):
        from .shell.widgets import ResearchSidebar

        for node in (self.screen, *list(getattr(self, "screen_stack", []) or [])):
            if node is None:
                continue
            try:
                return node.query_one("#shell-sidebar", ResearchSidebar)
            except Exception:  # noqa: BLE001
                continue
        return None

    def remove_workspace_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        confirmed: bool = False,
    ) -> None:
        """Drop a workspace resource. Unconfirmed calls arm the sidebar ✕."""
        if self.workspace is None:
            return
        if not confirmed:
            sidebar = self._shell_sidebar()
            if sidebar is not None:
                sidebar.remove_resource(resource_type, resource_id)
                return
        removed = self.workspace.remove_id(resource_id)
        if not removed:
            self.notify(f"{resource_id} not in workspace", severity="warning")
            return
        self.labels.pop(resource_id, None)
        self.persist_active_workspace()
        self.notify(f"Removed {resource_id}")
        # If we're looking at that resource, step back to a safe screen.
        try:
            action = (
                self.investigation.get_action(self.active.action)
                if self.investigation and self.active
                else None
            )
        except Exception:  # noqa: BLE001
            action = None
        viewing = False
        if action is not None and action.type == resource_type:
            ctx = getattr(action, "context", None)
            bucket = getattr(ctx, resource_type, None) if ctx is not None else None
            if isinstance(bucket, list) and resource_id in bucket:
                viewing = True
            elif action.args.get("series_id") == resource_id:
                viewing = True
        if viewing:
            self._show_front(reset=True)
        else:
            self.refresh_shell_chrome()

    def action_remove_active_sidebar_target(self) -> None:
        """Shift+X — close tab in Shift+T menu; else remove query/resource."""
        try:
            from ..actions.screen import BaseScreen
            from .shell import PaneBar

            if isinstance(self.screen, BaseScreen):
                pane = self.screen.query_one("#shell-pane-bar", PaneBar)
                if pane._jumping:
                    pane.cancel_jump()
                    self.close_investigation_tab()
                    return
        except Exception:  # noqa: BLE001
            pass
        self.remove_active_sidebar_target()

    def remove_active_sidebar_target(self) -> None:
        if self._invalid or not self.investigation or not self.active:
            return
        try:
            action = self.investigation.get_action(self.active.action)
        except Exception:  # noqa: BLE001
            return
        if action.type == "query":
            qid = action.args.get("query_id")
            if qid is not None:
                self.delete_query(int(qid), confirmed=False)
            return
        if action.type in {"entity", "metric", "series", "release"}:
            ctx = getattr(action, "context", None)
            rid = None
            if action.type == "series":
                rid = action.args.get("series_id")
            if rid is None and ctx is not None:
                bucket = getattr(ctx, action.type, None)
                if isinstance(bucket, list) and bucket:
                    rid = bucket[0]
            if rid:
                self.remove_workspace_resource(
                    action.type, str(rid), confirmed=False
                )
            return
        self.notify("Nothing to remove here — open a query or resource", severity="warning")

    # --- Onboarding: tutorials ------------------------------------------

    def action_tutorials(self) -> None:
        """Tutorial menu: start, switch, or leave a coached tutorial."""
        self._open_tutorial_menu_screen(False)

    def _open_tutorial_menu_screen(self, first_run: bool) -> None:
        menu = TutorialMenu(
            list_tutorials(),
            completed_tutorials(self.onboarding_dir),
            first_run=first_run,
            active_id=self.coach.tutorial.id if self.coach else None,
        )
        # Defer until the modal has fully popped (same as command dispatch).
        self.push_screen(menu, lambda choice: self.call_later(self._on_tutorial_choice, choice))

    def _on_tutorial_choice(self, choice: str | None) -> None:
        if choice is None:
            return
        if choice == LEAVE_TUTORIAL:
            self.leave_tutorial()
        else:
            self.start_tutorial(load_tutorial(choice))

    def _switch_investigation(
        self,
        investigation_id: str,
        action: int,
        *,
        workspace: int = DEFAULT_WORKSPACE_ID,
    ) -> None:
        self._persist_workspace_if_dirty()
        set_active(
            investigation_id,
            project=self.project_id,
            action=action,
            workspace=workspace,
            app_dir=self.app_dir,
        )
        self._nav_dest = None
        self._reload_everything(force_screen=True)

    def start_tutorial(self, tutorial: Tutorial) -> None:
        """Install the tutorial investigation fresh and switch to it."""
        # Remember the user's own investigation; chaining tutorials keeps it.
        if self.active is not None and not self.active.investigation.startswith(
            TUTORIAL_PREFIX
        ):
            self._pre_tutorial = (
                self.active.investigation,
                self.active.action,
                self.project_id,
            )
        inv = tutorial.build_investigation()
        project = Project.new(
            inv.id,
            title=inv.title or inv.id,
            notes=inv.notes,
            investigation_ids=[inv.id],
        )
        save_project_investigation(
            inv.id, inv, app_dir=self.app_dir, project=project
        )
        self.project_id = inv.id
        self.coach = TutorialCoach(tutorial)
        self._switch_investigation(tutorial.investigation_id, tutorial.start_action)

    def leave_tutorial(self) -> None:
        """Reopen the investigation that was active before the tutorial."""
        self.coach = None
        back, self._pre_tutorial = self._pre_tutorial, None
        self._sync_coach_bar()
        if back is not None:
            inv_id, action, project_id = back[0], back[1], back[2] if len(back) > 2 else None
            try:
                if project_id is None:
                    project_id = find_investigation_project(
                        inv_id, app_dir=self.app_dir
                    )
                if project_id is None:
                    raise InvestigationError("missing project")
                load_project_investigation(project_id, inv_id, app_dir=self.app_dir)
                self.project_id = project_id
                self._switch_investigation(inv_id, action)
                return
            except (InvestigationError, OSError):
                back = None
        if back is None:
            self.action_go_home()

    def coach_snapshot(self) -> CoachSnapshot:
        focus_ids: set[str] = set()
        node = self.focused
        while node is not None:
            if getattr(node, "id", None):
                focus_ids.add(node.id)
            node = getattr(node, "parent", None)
        screen = self.screen
        flags = getattr(screen, "coach_flags", None)
        return CoachSnapshot(
            metrics=tuple(self.workspace.metric),
            entities=tuple(self.workspace.entity),
            query_count=len(self.investigation.queries) if self.investigation else 0,
            active_action=self.active.action if self.active else None,
            focus_ids=frozenset(focus_ids),
            focus_in_input=isinstance(self.focused, Input),
            screen=type(screen).__name__,
            home_dest=self._nav_dest,
            screen_flags=frozenset(flags() if callable(flags) else ()),
        )

    def _poll_activity_marks(self) -> None:
        """Refresh tab ● marks from recent action-log activity."""
        if self._invalid or not self.project_id:
            return
        try:
            from ..ops.runner import get_store

            store = get_store(app_dir=self.app_dir)
            events = store.list_events(project_id=self.project_id, limit=30)
        except Exception:  # noqa: BLE001
            return
        marks: dict[str, str] = {}
        for event in events:
            inv_id = event.get("investigation_id")
            if not inv_id or inv_id in marks:
                continue
            if event.get("status") == "error":
                marks[inv_id] = "!"
            else:
                marks[inv_id] = "●"
        if marks != self._tab_activity_marks:
            self._tab_activity_marks = marks
            self.refresh_shell_chrome()

    def _poll_coach(self) -> None:
        snap = None
        if self.coach is not None:
            if self.active is None or (
                self.active.investigation != self.coach.tutorial.investigation_id
            ):
                # The user opened another investigation; the tutorial is over.
                self.coach = None
                self._pre_tutorial = None
            else:
                snap = self.coach_snapshot()
                if self.coach.advance(snap) and self.coach.is_finished:
                    self._finish_tutorial()
        self._sync_coach_bar(snap)
        self._sync_tip_bar(snap)

    def _finish_tutorial(self) -> None:
        """Last step done: record it, drop the coach, and ask what's next."""
        assert self.coach is not None
        tutorial = self.coach.tutorial
        phase = self.coach.current_phase
        mark_completed(self.onboarding_dir, tutorial.id)
        self.coach = None
        ids = [t.id for t in list_tutorials()]
        later = ids[ids.index(tutorial.id) + 1 :] if tutorial.id in ids else []
        next_tutorial = load_tutorial(later[0]) if later else None
        self.push_screen(
            TutorialComplete(tutorial, phase.text if phase else "", next_tutorial),
            lambda choice: self.call_later(self._on_complete_choice, choice),
        )

    def _on_complete_choice(self, choice: str | None) -> None:
        if choice == COMPLETE_MENU:
            self._open_tutorial_menu_screen(False)
        elif choice == COMPLETE_BACK:
            self.leave_tutorial()
        elif choice and choice != COMPLETE_STAY:
            self.start_tutorial(load_tutorial(choice))

    def _guide_screen(self, docked: Screen | None) -> Screen | None:
        """Screen a guide bar belongs on: the front screen, or the drawer /
        palette on top of it; other modals keep it on the screen beneath."""
        screen = self.screen
        if isinstance(screen, ModalScreen) and not isinstance(
            screen,
            (WorkspaceDrawer, SearchDrawer, ActivityDrawer, CommandPalette),
        ):
            return docked
        return screen

    # --- Onboarding: contextual tips ---------------------------------------

    def set_tips(self, enabled: bool) -> None:
        """Turn tips on or off (remembered across sessions)."""
        self._tips_on = enabled
        set_tips_enabled(self.onboarding_dir, enabled)
        self._sync_tip_bar()
        if enabled:
            self.notify("Tips on. Hide them with :tips off or the ✕ in the corner.")
        else:
            self.notify("Tips off. Bring them back with :tips on")

    def _sync_tip_bar(self, snap: CoachSnapshot | None = None) -> None:
        """Keep the tip box on the current screen while tips are on and no
        tutorial is running (the coach bar takes over during one)."""
        want = self._tips_on and self.coach is None and not self._invalid
        screen = self._guide_screen(self._tip_screen)
        bar = self._tip_bar
        if bar is not None and (not want or self._tip_screen is not screen):
            if bar.is_attached:
                bar.remove()
            self._tip_bar = self._tip_screen = None
            self._tip_shown = None
        if not want or screen is None:
            return
        text = tip_for(snap or self.coach_snapshot())
        if self._tip_bar is None:
            self._tip_bar = TipBox(text)
            self._tip_screen = screen
            screen.mount(self._tip_bar)
        elif text != self._tip_shown:
            self._tip_bar.update_text(text)
        self._tip_shown = text

    def _sync_coach_bar(self, snap: CoachSnapshot | None = None) -> None:
        """Keep the coach docked on the current action screen (or remove it).

        Action screens are swapped with ``switch_screen``, so the bar is
        remounted whenever the front screen changes. It follows the user into
        the drawers and the palette (tutorial steps happen there); other
        modals leave it on the screen beneath.
        """
        screen = self._guide_screen(self._coach_screen)
        bar = self._coach_bar
        if bar is not None and (self.coach is None or self._coach_screen is not screen):
            if bar.is_attached:
                bar.remove()
            self._coach_bar = self._coach_screen = None
            self._coach_shown = None
        if self.coach is None or screen is None or self._invalid:
            return
        text = self.coach.render(snap or self.coach_snapshot())
        if self._coach_bar is None:
            self._coach_bar = CoachBar(*text)
            self._coach_screen = screen
            screen.mount(self._coach_bar)
        elif text != self._coach_shown:
            self._coach_bar.update_text(*text)
        self._coach_shown = text


def run_investigation_host(
    client: JSTDataClient, *, app_dir: Optional[Path] = None
) -> None:
    """Launch the investigation TUI (after create/load has set active)."""
    ensure_bootstrap(app_dir=app_dir)
    host = InvestigationHost(client, app_dir=app_dir)
    host.run()
    finish_publish_after_exit(host)


def finish_publish_after_exit(host: InvestigationHost) -> None:
    """If the TUI closed mid-publish, wait here and print the link (JEF-170).

    The terminal is back by now; Ctrl+C stops waiting.
    """
    thread = host.publish_thread
    if thread is not None and thread.is_alive():
        print(
            "Still publishing… the link will be printed here. "
            "Press Ctrl+C to stop waiting.",
            flush=True,
        )
        try:
            while thread.is_alive():
                thread.join(0.2)
        except KeyboardInterrupt:
            print(
                "\nStopped waiting. The page may still be created: check "
                "Publications on your dashboard before publishing again."
            )
            return
    outcome = host.publish_outcome
    if outcome is None:
        return
    if outcome.error is not None:
        error = outcome.error
        if not error.startswith("Publish failed"):
            error = f"Publish failed: {error}"
        print(error)
    else:
        verb = "Updated" if outcome.updated else "Published"
        print(f"{verb}: {outcome.url}")
