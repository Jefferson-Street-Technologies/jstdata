"""Investigation host: tree navigation, command mode, drawers, hot reload."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, Static

from ..client import JSTDataClient
from ..actions.base import get_action, hydrate_labels
from ..actions.help import HelpScreen
from .active import ActiveContext, active_path, load_active, save_active
from .api import (
    add_action,
    remove_action,
    persist_investigation,
)
from .bootstrap import ensure_bootstrap
from .command_palette import CommandPalette
from .commands import CommandError, ParsedCommand, parse_command_line
from .model import Investigation, InvestigationError, Workspace
from .search_drawer import SearchDrawer
from .store import investigation_path, load_investigation
from .workspace_drawer import WorkspaceDrawer

from . import home as _home  # noqa: F401
import jstdata.actions  # noqa: F401

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
        Binding("n", "next_action", "Next action"),
        Binding("p", "prev_action", "Prev action"),
        Binding("N", "next_workspace", "Next workspace", key_display="N"),
        Binding("P", "prev_workspace", "Prev workspace", key_display="P"),
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
        self.workspace = Workspace()
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

    def _active_file(self) -> Path:
        return active_path(app_dir=self.app_dir)

    def _inv_file(self) -> Path:
        assert self.active is not None
        return investigation_path(self.active.investigation, root=self.inv_root)

    def on_mount(self) -> None:
        ensure_bootstrap(app_dir=self.app_dir)
        self._reload_everything(force_screen=True)
        self.set_interval(POLL_INTERVAL, self._poll_files)

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
            self.investigation = load_investigation(
                self.active.investigation, root=self.inv_root
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
        hydrate_labels(self.client, self.workspace, self.labels)
        self.refresh_workspace_drawer()

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
        if self._invalid or not self.investigation or not self.active:
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

        kwargs = dict(action.args)
        if action.type == "home":
            kwargs.setdefault("title", self.investigation.title)
            body = action.description or ""
            if ws.description:
                body = (body + "\n\n" if body else "") + f"Workspace: {ws.description}"
            kwargs.setdefault("body", body)
            label = ws.label or f"workspace {ws.id}"
            ws_pos = (
                f"{self._ws_order.index(ws.id) + 1}/{len(self._ws_order)}"
                if ws.id in self._ws_order
                else str(ws.id)
            )
            kwargs.setdefault(
                "meta",
                f"action {action.id}/{self._order[-1] if self._order else 0}  ·  "
                f"{label} ({ws_pos})  ·  : commands  ·  n/p actions  ·  "
                f"N/P workspaces  ·  ? help  ·  q quit",
            )

        return spec.create_screen(self.client, self.workspace, **kwargs)

    def _pop_transient_overlays(self) -> None:
        """Dismiss command palette / confirm; leave drawers alone when possible."""
        while len(self.screen_stack) > 2 and isinstance(
            self.screen, (CommandPalette, ConfirmForceModal)
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

    def action_next_action(self) -> None:
        self._navigate_action(+1)

    def action_prev_action(self) -> None:
        self._navigate_action(-1)

    def action_next_workspace(self) -> None:
        self._navigate_workspace(+1)

    def action_prev_workspace(self) -> None:
        self._navigate_workspace(-1)

    def _navigate_action(self, delta: int) -> None:
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
        label = self.investigation.workspaces[new_ws].label or f"workspace {new_ws}"
        self.notify(f"Workspace {label}", timeout=2)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {
            "command_palette",
            "next_action",
            "prev_action",
            "next_workspace",
            "prev_workspace",
            "show_help",
        }:
            if isinstance(self.focused, Input):
                return False
        return True

    def action_command_palette(self) -> None:
        if self._invalid:
            return
        self.push_screen(
            CommandPalette(history=self._command_history),
            self._on_command_submitted,
        )

    def _on_command_submitted(self, line: str | None) -> None:
        if not line:
            return
        if line not in self._command_history:
            self._command_history.append(line)
        try:
            parsed = parse_command_line(line)
        except CommandError as exc:
            self.notify(str(exc), severity="error")
            return
        self._dispatch_command(parsed)

    def _dispatch_command(self, parsed: ParsedCommand) -> None:
        if parsed.name == "workspace":
            self._open_workspace_drawer()
        elif parsed.name == "search":
            self._open_search_drawer(parsed.args[0], parsed.args[1])
        elif parsed.name == "add":
            self._cmd_add(parsed)
        elif parsed.name == "rm":
            self._cmd_rm(parsed, force=bool(parsed.options.get("force")))

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
        try:
            action = add_action(
                self.investigation,
                action_type,
                parent_id=parsed.options.get("parent"),
                args=parsed.options.get("args") or {},
                description=parsed.options.get("description"),
                activate=bool(parsed.options.get("activate", True)),
                app_dir=self.app_dir,
            )
        except InvestigationError as exc:
            self.notify(str(exc), severity="error")
            return
        self._order = self.investigation.bfs_order()
        self.active = load_active(app_dir=self.app_dir)
        self.notify(
            f"Added action {action.id} ({action.type})"
            + (" — activated" if parsed.options.get("activate", True) else "")
        )
        if parsed.options.get("activate", True):
            self._show_current_action(reset=True)

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
        spec = self.current_action_spec
        self.push_screen(HelpScreen(spec.name, list(spec.bindings)))


def run_investigation_host(
    client: JSTDataClient, *, app_dir: Optional[Path] = None
) -> None:
    """Launch the investigation TUI (after create/load has set active)."""
    ensure_bootstrap(app_dir=app_dir)
    InvestigationHost(client, app_dir=app_dir).run()
