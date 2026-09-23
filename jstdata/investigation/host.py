"""Investigation host: tree navigation, hot reload, shared step chrome."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, Static

from ..client import JSTDataClient
from ..session import Session
from ..workflows.base import hydrate_labels
from ..workflows.export import ExportModal
from ..workflows.find import FindModal
from ..workflows.host import HelpScreen
from ..workflows.session_manager import SessionManagerModal
from .active import ActiveContext, active_path, load_active, save_active
from .api import sync_workspace_from_session
from .bootstrap import ensure_bootstrap
from .model import Investigation, InvestigationError, Workspace
from .store import investigation_path, load_investigation

# Ensure step catalog includes home + built-in analytical steps.
from . import home as _home  # noqa: F401
import jstdata.workflows  # noqa: F401

from ..workflows.base import get_step

POLL_INTERVAL = 0.4


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
        Binding("question_mark", "show_help", "Step help", key_display="?"),
        Binding("s", "session", "Session"),
        Binding("f", "find", "Find"),
        Binding("e", "export", "Export"),
        Binding("n", "next_action", "Next"),
        Binding("p", "prev_action", "Prev"),
    ]

    def __init__(
        self,
        client: JSTDataClient,
        *,
        app_dir: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.app_dir = app_dir
        self.inv_root = None if app_dir is None else app_dir / "investigations"
        self.investigation: Investigation | None = None
        self.active: ActiveContext | None = None
        self.session = Session()
        self.labels: dict[str, str] = {}
        self._order: list[int] = []
        self._inv_mtime: float | None = None
        self._active_mtime: float | None = None
        self._own_active_mtime: float | None = None
        self._ws_fingerprint: tuple | None = None
        self._invalid = False
        self._suppress_poll = False

    # --- paths -------------------------------------------------------------

    def _active_file(self) -> Path:
        return active_path(app_dir=self.app_dir)

    def _inv_file(self) -> Path:
        assert self.active is not None
        return investigation_path(self.active.investigation, root=self.inv_root)

    # --- bootstrap / load --------------------------------------------------

    def on_mount(self) -> None:
        ensure_bootstrap(app_dir=self.app_dir)
        self._reload_everything(force_screen=True)
        self.set_interval(POLL_INTERVAL, self._poll_files)

    def remember_label(self, resource_id: str, label: str) -> None:
        self.labels[resource_id] = label or resource_id

    @property
    def step_count(self) -> int:
        return max(len(self._order), 1)

    @property
    def step_index(self) -> int:
        if not self.active or not self._order:
            return 0
        try:
            return self._order.index(self.active.action)
        except ValueError:
            return 0

    @property
    def current_step_spec(self):
        if not self.investigation or not self.active:
            return get_step("home")
        try:
            action = self.investigation.get_action(self.active.action)
            return get_step(action.type)
        except (InvestigationError, KeyError):
            return get_step("home")

    # --- core reload -------------------------------------------------------

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
            self.investigation = load_investigation(
                self.active.investigation, root=self.inv_root
            )
            self.investigation.validate()
            ws = self.investigation.get_workspace(self.active.workspace)
            _ = self.investigation.get_action(self.active.action)
            self._apply_workspace(ws)
            self._order = self.investigation.bfs_order()
            self._invalid = False
            self._snapshot_mtimes()
            if force_screen or not isinstance(self.screen, Screen):
                self._show_current_action(reset=True)
            else:
                self._show_current_action(reset=force_screen)
        except (InvestigationError, OSError, KeyError) as exc:
            self._enter_invalid(str(exc))

    def _apply_workspace(self, ws: Workspace) -> None:
        self.session = ws.to_session()
        self._ws_fingerprint = ws.resource_fingerprint()
        self.labels = {}
        hydrate_labels(self.client, self.session, self.labels)

    def _enter_invalid(self, message: str) -> None:
        self._invalid = True
        self.investigation = None
        self._order = []
        while len(self.screen_stack) > 1:
            self.pop_screen()
        err = InvalidStateScreen(message)
        if isinstance(self.screen, InvalidStateScreen):
            self.switch_screen(err)
        else:
            self.push_screen(err)

    def _show_current_action(self, *, reset: bool = False) -> None:
        if self._invalid or not self.investigation or not self.active:
            return
        screen = self._make_action_screen()
        # Keep default screen + one action (+ optional modals).
        self._pop_overlays()
        if len(self.screen_stack) <= 1:
            self.push_screen(screen)
        else:
            self.switch_screen(screen)

    def _make_action_screen(self) -> Screen:
        assert self.investigation is not None and self.active is not None
        action = self.investigation.get_action(self.active.action)
        ws = self.investigation.get_workspace(self.active.workspace)
        try:
            spec = get_step(action.type)
        except KeyError as exc:
            raise InvestigationError(
                f"Unknown action type {action.type!r} (action {action.id})."
            ) from exc

        kwargs = dict(action.args)
        if action.type == "home":
            kwargs.setdefault("title", self.investigation.title)
            body = action.description or ""
            if ws.description:
                body = (body + "\n\n" if body else "") + f"Workspace: {ws.description}"
            kwargs.setdefault("body", body)
            label = ws.label or f"workspace {ws.id}"
            kwargs.setdefault(
                "meta",
                f"action {action.id}/{self._order[-1] if self._order else 0}  ·  "
                f"{label}  ·  n/p navigate  ·  ? help  ·  q quit",
            )

        return spec.create_screen(self.client, self.session, **kwargs)

    def _pop_overlays(self) -> None:
        while len(self.screen_stack) > 2:
            self.pop_screen()

    # --- persistence helpers -----------------------------------------------

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
        # Reflect live session into workspace model for comparison.
        probe = Workspace(id=ws.id, label=ws.label, description=ws.description)
        probe.apply_session(self.session)
        if probe.resource_fingerprint() == self._ws_fingerprint:
            return
        self._suppress_poll = True
        try:
            sync_workspace_from_session(
                self.investigation,
                self.active.workspace,
                self.session,
                app_dir=self.app_dir,
            )
            self._ws_fingerprint = probe.resource_fingerprint()
            try:
                self._inv_mtime = self._inv_file().stat().st_mtime
            except OSError:
                pass
        finally:
            self._suppress_poll = False

    def _poll_files(self) -> None:
        if self._suppress_poll:
            return
        # Persist workspace edits from step UIs (console/find/etc.).
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
        # Force screen rebuild when focus or investigation identity changes,
        # or whenever the investigation YAML changed.
        if (
            inv_changed
            or self.active.action != prev_action
            or self.active.workspace != prev_ws
            or self.active.investigation != prev_inv
        ):
            self._show_current_action(reset=True)
        self.notify("Investigation reloaded", timeout=2)

    # --- navigation --------------------------------------------------------

    def action_next_action(self) -> None:
        self._navigate(+1)

    def action_prev_action(self) -> None:
        self._navigate(-1)

    def _navigate(self, delta: int) -> None:
        if self._invalid or not self.active or not self._order:
            self.notify("No actions to navigate", severity="warning")
            return
        try:
            idx = self._order.index(self.active.action)
        except ValueError:
            idx = 0
        new_idx = idx + delta
        if new_idx < 0:
            self.notify("First action", severity="warning")
            return
        if new_idx >= len(self._order):
            self.notify("Last action", severity="warning")
            return
        new_action = self._order[new_idx]
        ctx = ActiveContext(
            investigation=self.active.investigation,
            workspace=self.active.workspace,
            action=new_action,
        )
        self._write_active(ctx)
        self._show_current_action(reset=True)

    # --- shared chrome (mirrors WorkflowHost) ------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {
            "session",
            "find",
            "export",
            "next_action",
            "prev_action",
            "show_help",
        }:
            if isinstance(self.focused, Input):
                return False
        return True

    def action_session(self) -> None:
        self.push_screen(
            SessionManagerModal(self.client, self.session, self.labels)
        )

    def action_find(self) -> None:
        self.push_screen(FindModal(self.client, self.session))

    def action_export(self) -> None:
        default = f"{self.active.investigation if self.active else 'session'}.json"
        self.push_screen(ExportModal(self.session, default_path=default))

    def action_back(self) -> None:
        focused = self.focused
        screen = self.screen
        if getattr(focused, "id", None) in (
            "metrics-filter",
            "search-input",
            "rank-filter",
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

    def action_show_help(self) -> None:
        spec = self.current_step_spec
        self.push_screen(HelpScreen(spec.name, list(spec.bindings)))


def run_investigation_host(
    client: JSTDataClient, *, app_dir: Optional[Path] = None
) -> None:
    """Launch the investigation TUI (after create/load has set active)."""
    ensure_bootstrap(app_dir=app_dir)
    InvestigationHost(client, app_dir=app_dir).run()
