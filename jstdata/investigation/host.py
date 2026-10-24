"""Investigation host: tree navigation, command mode, drawers, hot reload."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import Input, Label, Static

from ..client import JSTDataClient
from ..actions.base import ActionBinding, get_action, hydrate_labels
from ..actions.help import HelpScreen
from .active import ActiveContext, active_path, load_active, save_active
from .api import (
    add_action,
    add_query,
    remove_action,
    remove_query,
    persist_investigation,
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
from .search_drawer import SearchDrawer
from .store import investigation_path, load_investigation
from .workspace_drawer import WorkspaceDrawer
from ..actions.drill import DrillDownModal

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
        # ``dd`` is handled in on_key — Textual commas mean alternate keys, not chords.
    ]

    # Seconds to wait for the second ``d`` (vim-style).
    _DD_TIMEOUT = 0.45

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
        self._nav_dest: str | None = None
        self._nav_return: str | None = None
        self._dest_index: dict[str, int] = {}

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
            if isinstance(screen, WorkspaceBrowser):
                screen.workspace = self.workspace
                screen.labels = self.labels
                screen.rebuild()
        if isinstance(self.screen, HomeScreen):
            self.screen.refresh_live()

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
        self._show_front(reset=reset)

    def _show_front(self, *, reset: bool = False) -> None:
        if self._invalid or not self.investigation or not self.active:
            return
        if isinstance(
            self.screen, (CommandPalette, ConfirmForceModal, DrillDownModal)
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
            kwargs.setdefault("title", self.investigation.title)
            body = action.description or ""
            if ws.description:
                body = (body + "\n\n" if body else "") + f"Workspace: {ws.description}"
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
            self.screen, (CommandPalette, ConfirmForceModal, DrillDownModal)
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
        self._d_timer = self.set_timer(self._DD_TIMEOUT, self._clear_pending_d)
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
        name = query.name or f"query {query_id}"
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


def run_investigation_host(
    client: JSTDataClient, *, app_dir: Optional[Path] = None
) -> None:
    """Launch the investigation TUI (after create/load has set active)."""
    ensure_bootstrap(app_dir=app_dir)
    InvestigationHost(client, app_dir=app_dir).run()
