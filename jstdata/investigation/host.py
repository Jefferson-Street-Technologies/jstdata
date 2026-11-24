"""Investigation host: tree navigation, command mode, drawers, hot reload."""

from __future__ import annotations

from rich.markup import escape

import asyncio
import contextvars
import copy
import threading
import time
from pathlib import Path
from typing import NamedTuple, Optional

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
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
    Investigation,
    InvestigationError,
    ResearchContext,
    ROOT_ACTION_ID,
    Workspace,
    resolve_input_context,
)
from .browsers import DestinationScreen, WorkspaceBrowser
from .home import HomeScreen
from .nav import DEST_IDS, parse_goto
from .publish_screens import (
    PublishConfirmModal,
    PublishDraftScreen,
    PublishedModal,
)
from .search_drawer import SearchDrawer
from .project_store import (
    find_investigation_project,
    load_project_investigation,
    project_investigations_dir,
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


class RenameModal(ModalScreen[tuple[str, str] | None]):
    """One form: investigation title and description."""

    DEFAULT_CSS = """
    RenameModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #rename-box {
        width: 64;
        height: auto;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #rename-kicker { color: #4ade80; text-style: bold; margin-bottom: 1; }
    #rename-title, #rename-notes {
        width: 100%;
        background: #0a0a0a;
        border: solid #333;
        margin-bottom: 1;
    }
    #rename-hint { color: #888; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, title: str, description: str = "") -> None:
        super().__init__()
        self._title = title
        self._description = description

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-box"):
            yield Label("INVESTIGATION", id="rename-kicker")
            yield Input(self._title, placeholder="Title", id="rename-title")
            yield Input(
                self._description, placeholder="Description", id="rename-notes"
            )
            yield Label(
                "[bold]enter[/bold] save  ·  [bold]esc[/bold] cancel",
                id="rename-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one("#rename-title", Input).focus()

    @on(Input.Submitted)
    def on_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "rename-title":
            self.query_one("#rename-notes", Input).focus()
            return
        self._save()

    def _save(self) -> None:
        title = self.query_one("#rename-title", Input).value.strip()
        if not title:
            self.query_one("#rename-title", Input).focus()
            return
        notes = self.query_one("#rename-notes", Input).value.strip()
        self.dismiss((title, notes))

    def action_cancel(self) -> None:
        self.dismiss(None)


class AnnotationModal(ModalScreen[str | None]):
    """Edit freeform annotations (investigation or query)."""

    DEFAULT_CSS = """
    AnnotationModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #anno-box {
        width: 72;
        height: 22;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #anno-kicker { color: #4ade80; text-style: bold; height: 1; margin-bottom: 1; }
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
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, *, title: str, text: str = "") -> None:
        super().__init__()
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="anno-box"):
            yield Label(self._title, id="anno-kicker")
            yield TextArea(self._text, id="anno-body")
            yield Label(
                "[bold]ctrl+s[/bold] save  ·  [bold]esc[/bold] cancel",
                id="anno-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one("#anno-body", TextArea).focus()

    def action_save(self) -> None:
        text = self.query_one("#anno-body", TextArea).text
        self.dismiss(text)

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
        Binding("question_mark", "show_help", "Action help", key_display="?"),
        Binding(":", "command_palette", "Command", key_display=":"),
        Binding("g", "go_home", "Home", key_display="g"),
        # Tree: siblings laterally, depth via N/P (and arrows).
        Binding("n", "next_sibling", "Next sibling"),
        Binding("p", "prev_sibling", "Prev sibling"),
        Binding("down", "next_sibling", "Next sibling", show=False),
        Binding("up", "prev_sibling", "Prev sibling", show=False),
        Binding("N", "child_action", "Into child", key_display="N"),
        Binding("P", "parent_action", "To parent", key_display="P"),
        Binding("right", "child_action", "Into child", show=False),
        Binding("left", "parent_action", "To parent", show=False),
        Binding("t", "tutorials", "Tutorials", key_display="t"),
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
        self.investigation: Investigation | None = None
        self.active: ActiveContext | None = None
        self.workspace = Workspace()
        self.input_context = ResearchContext()
        self.labels: dict[str, str] = {}
        self._order: list[int] = []
        self._ws_order: list[int] = []
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

    def _hydrate_labels_worker(self) -> None:
        labels: dict[str, str] = {}
        try:
            hydrate_labels(self.client, self.workspace, labels)
        except OSError as exc:
            msg = f"Label cache unavailable: {exc}"
            self.call_from_thread(lambda: self.notify(msg, severity="warning"))
            return
        self.call_from_thread(self._apply_hydrated_labels, labels)

    def _apply_hydrated_labels(self, labels: dict[str, str]) -> None:
        self.labels.update(labels)
        # Compose may still be attaching Home chrome when /resource returns.
        self.call_later(self.refresh_workspace_drawer)

    def _enter_invalid(self, message: str) -> None:
        self._invalid = True
        self.investigation = None
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
                RenameModal,
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
            ctx = ActiveContext(
                investigation=self.active.investigation,
                workspace=self.active.workspace,
                action=ROOT_ACTION_ID,
            )
            self._write_active(ctx)
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
                RenameModal,
                ObservationDetail,
            ),
        ):
            self.pop_screen()

    def _write_active(self, ctx: ActiveContext) -> None:
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
                CommandPalette,
                DrillDownModal,
                DestinationScreen,
                HomeScreen,
                RenameModal,
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

    def _activate_action(self, action_id: int) -> None:
        self._leave_destination_for_action()
        assert self.active is not None
        ctx = ActiveContext(
            investigation=self.active.investigation,
            workspace=self.active.workspace,
            action=action_id,
        )
        self._write_active(ctx)
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
        ctx = ActiveContext(
            investigation=self.active.investigation,
            workspace=new_ws,
            action=self.active.action,
        )
        self._write_active(ctx)
        assert self.investigation is not None
        self._apply_workspace(self.investigation.get_workspace(new_ws))
        self._show_current_action(reset=True)
        label = self.investigation.workspace.label or "workspace"
        self.notify(f"Workspace {label}", timeout=2)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "go_home" and isinstance(self.focused, Input):
            return False
        tree_actions = {
            "tutorials",
            "command_palette",
            "next_sibling",
            "prev_sibling",
            "child_action",
            "parent_action",
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
                    CommandPalette,
                    DrillDownModal,
                    DestinationScreen,
                    HomeScreen,
                    RenameModal,
                    ObservationDetail,
                ),
            ):
                return False
        return True

    def action_go_home(self) -> None:
        """Reorient: dismiss overlays and activate the root home action."""
        if self._invalid or not self.investigation or not self.active:
            return
        self._nav_dest = None
        self._nav_return = None
        while len(self.screen_stack) > 2:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen) and self.active.action == ROOT_ACTION_ID:
            self.screen.leave_destination()
            self.notify("Home", timeout=1)
            return
        if self.active.action == ROOT_ACTION_ID:
            self._show_front(reset=True)
            self.notify("Home", timeout=1)
            return
        self._activate_action(ROOT_ACTION_ID)

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

    def query_from_workspace(self) -> None:
        """Snapshot the workspace bag into a saved query and open it."""
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
            query = add_query(
                self.investigation,
                from_workspace=True,
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Saved query {query.id}")
        self.open_query_view(query.id)

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
        elif parsed.name == "annotate":
            self._cmd_annotate(parsed)
        elif parsed.name == "publish":
            self._cmd_publish()
        elif parsed.name == "tutorial":
            self._open_tutorial_menu_screen(False)
        elif parsed.name == "tips":
            state = parsed.options.get("state")
            self.set_tips(not self._tips_on if state is None else bool(state))

    def _cmd_publish(self) -> None:
        """Draft the page's text, confirm, then publish in a background thread."""
        inv = self.investigation
        if not inv:
            self.notify("No investigation loaded", severity="error")
            return
        if self._publishing:
            self.notify("Already publishing…")
            return
        if not inv.queries:
            self.notify(
                "Nothing to publish yet. Save a query first with :query.",
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
            self.push_screen(
                RenameModal(
                    self.investigation.title,
                    self.investigation.notes or "",
                ),
                self._on_rename_submitted,
            )
            return
        self._apply_rename(title=title, notes=description)

    def _on_rename_submitted(self, result: tuple[str, str] | None) -> None:
        if not result:
            return
        title, notes = result
        self._apply_rename(title=title, notes=notes)

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
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_live()
        bits = []
        if title is not None:
            bits.append(f"title {title!r}")
        if notes is not None:
            bits.append("description updated" if notes.strip() else "description cleared")
        self.notify("Renamed: " + ", ".join(bits) if bits else "Saved")

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

    def edit_query_annotations(self, query_id: int) -> None:
        """Open the annotation editor for a saved query."""
        if not self.investigation:
            self.notify("No investigation loaded", severity="error")
            return
        try:
            query = self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        label = escape(query.name or f"Q{query.id}")
        self.push_screen(
            AnnotationModal(
                title=f"QUERY ANNOTATIONS · {label.upper()}",
                text=query.annotations or "",
            ),
            lambda result, qid=query_id: self._on_query_annotations_submitted(
                qid, result
            ),
        )

    def _on_query_annotations_submitted(
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
                    annotations=result,
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
        self._refresh_annotation_surfaces()
        cleared = not result.strip()
        self.notify(
            f"Q{query_id} annotations cleared"
            if cleared
            else f"Q{query_id} annotations saved"
        )

    def _refresh_annotation_surfaces(self) -> None:
        """Repaint Home / query browsers after annotation edits."""
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_live()
            refresh = getattr(screen, "refresh_annotations", None)
            if callable(refresh):
                refresh()

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

    def action_show_help(self) -> None:
        if isinstance(self.screen, DestinationScreen):
            bindings = [
                ActionBinding(key, "help", desc)
                for key, desc in self.screen.help_bindings()
            ]
            self.push_screen(HelpScreen(self.screen.dest_title(), bindings))
            return
        spec = self.current_action_spec
        self.push_screen(HelpScreen(spec.name, list(spec.bindings)))

    def delete_query(self, query_id: int) -> None:
        if not self.investigation:
            return
        try:
            query = self.investigation.get_query(query_id)
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        name = escape(query.name or f"query {query_id}")
        self.push_screen(
            ConfirmForceModal(
                f"Delete Q{query_id} · {name}? This cannot be undone."
            ),
            lambda ok, qid=query_id: self._confirm_delete_query(qid) if ok else None,
        )

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

    def _switch_investigation(self, investigation_id: str, action: int) -> None:
        self._persist_workspace_if_dirty()
        set_active(
            investigation_id,
            project=self.project_id,
            action=action,
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
            screen, (WorkspaceDrawer, SearchDrawer, CommandPalette)
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
