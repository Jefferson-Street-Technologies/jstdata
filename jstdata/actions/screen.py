"""Shared base screen for investigation actions (research shell chrome)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label

from ..investigation.bug_report_modal import BugReportPanel
from ..investigation.shell import PaneBar, ProjectHeader, ResearchSidebar


class BaseScreen(Screen):
    """Action screen with persistent research shell chrome.

    Layout: project header → (sidebar | pane bar + body) → footer.
    Subclasses implement :meth:`compose_body`. Drawers/modals stay standalone.

    Pane bar: Home (H) / Search (/) / Commands (:); tabs always on the
    right with ``+`` (new) and ``×`` (close when 2+). ``T`` opens the tab
    menu (digits switch, ``N`` new, ``X`` close). ``Q`` / ``w`` number
    sidebar rows. Esc cancels jump.
    """

    # Keep the footer minimal — Home / Search live on the pane bar.
    FOOTER_SHOW_HOME: bool = False
    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]/[/bold] Search",
        "[bold]:[/bold] Commands",
        "[bold]Esc[/bold] Back",
    )

    # Shell-level bindings (host also owns some; these help when focus is here).
    BINDINGS = [
        Binding("slash", "shell_search", "Search", show=False),
        Binding("H", "shell_home", "Home", show=False),
        Binding("F", "shell_feedback", "Feedback", show=False),
        Binding("N", "shell_new_tab", "New tab", show=False),
        Binding("T", "shell_tab_jump", "Jump tabs", show=False),
        # Shift+X — lowercase x is chart series toggle on QueryScreen.
        Binding("X", "shell_remove", "Remove tile", show=False),
        # Shift+Q — plain q quits at the host. w opens workspace jump.
        Binding("Q", "sidebar_jump_queries", "Jump queries", show=False),
        Binding("w", "sidebar_jump_workspace", "Jump workspace", show=False),
        Binding("W", "sidebar_jump_workspace", "Jump workspace", show=False),
        Binding("1", "sidebar_digit_1", "Jump pick 1", show=False),
        Binding("2", "sidebar_digit_2", "Jump pick 2", show=False),
        Binding("3", "sidebar_digit_3", "Jump pick 3", show=False),
        Binding("4", "sidebar_digit_4", "Jump pick 4", show=False),
        Binding("5", "sidebar_digit_5", "Jump pick 5", show=False),
        Binding("6", "sidebar_digit_6", "Jump pick 6", show=False),
        Binding("7", "sidebar_digit_7", "Jump pick 7", show=False),
        Binding("8", "sidebar_digit_8", "Jump pick 8", show=False),
        Binding("9", "sidebar_digit_9", "Jump pick 9", show=False),
        Binding("0", "sidebar_digit_0", "Jump pick 10", show=False),
        Binding("ctrl+l", "focus_pane_main", "Focus main", show=False),
        Binding("backslash", "toggle_sidebar", "Toggle sidebar", show=False),
        # Screen-level Esc so jump/bug cancel isn't lost when the host
        # binding is shadowed (check_action used to disable this key).
        Binding("escape", "shell_escape", "Back", show=False),
    ]

    DEFAULT_CSS = """
    BaseScreen {
        background: #0a0a0a;
        layout: vertical;
    }

    #shell-header {
        height: 1;
        width: 100%;
    }

    /* Full-width strip between project header and sidebar|main.
       Must stay in normal flow (not dock) — docked Horizontals collapse to height 0. */
    #shell-pane-bar {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
    }

    #shell-main {
        height: 1fr;
        width: 100%;
    }

    #shell-body {
        width: 1fr;
        height: 1fr;
    }

    #shell-footer {
        dock: bottom;
        height: auto;
        width: 100%;
        background: #0f0f0f;
    }

    #cmd-bar {
        height: 3;
        background: #0f0f0f;
        border-top: solid #4ade80;
        align: left middle;
        padding: 0 1;
    }

    #shell-footer.-bug-open #cmd-bar {
        border-top: solid #333;
    }

    #help-hint {
        color: #888;
        width: 1fr;
        overflow: hidden;
    }

    #bug-report-hint {
        width: auto;
        color: #fbbf24;
        text-style: bold;
        padding: 0 1;
    }

    #shell-footer.-bug-open #bug-report-hint {
        color: #888;
        text-style: none;
    }
    """

    # Screens that should not wrap body in the research shell chrome.
    SHELL_ENABLED: bool = True

    def compose(self) -> ComposeResult:
        if self.SHELL_ENABLED:
            yield ProjectHeader(id="shell-header")
            yield PaneBar(id="shell-pane-bar")
            with Horizontal(id="shell-main"):
                yield ResearchSidebar(id="shell-sidebar")
                with Vertical(id="shell-body"):
                    yield from self.compose_body()
        else:
            yield from self.compose_body()
        with Vertical(id="shell-footer"):
            yield BugReportPanel()
            with Horizontal(id="cmd-bar"):
                yield Label("", id="help-hint", markup=True)
                yield Label(
                    "[Report issue]  !",
                    id="bug-report-hint",
                    markup=False,
                )

    def compose_body(self) -> ComposeResult:
        """Yield the screen body above the shared footer."""
        yield from ()

    def on_mount(self) -> None:
        self.refresh_footer()
        self.refresh_shell_chrome()
        self.refresh_breadcrumb()

    def shell_breadcrumb(self) -> str:
        """Contextual trail for the main pane (override per screen)."""
        return "Main"

    def refresh_breadcrumb(self) -> None:
        # Pane bar replaced the old main-pane kicker breadcrumb.
        return

    def refresh_shell_chrome(self) -> None:
        """Pull project/tabs/sidebar state from the host."""
        if not self.SHELL_ENABLED:
            return
        refresh = getattr(self.app, "refresh_shell_chrome", None)
        if callable(refresh):
            try:
                refresh(self)
            except Exception:  # noqa: BLE001
                pass

    def footer_status(self) -> list[str]:
        """Leading status chips (action id, scope, …). Tabs live in PaneBar."""
        parts: list[str] = []
        n = getattr(self.app, "action_count", 1)
        i = getattr(self.app, "action_index", 0) + 1
        spec = getattr(self.app, "current_action_spec", None)
        action_id = getattr(spec, "id", None) or "action"
        parts.append(f"{action_id} {i}/{n}")
        return parts

    def footer_action_keys(self) -> list[str]:
        """Screen-local key hints (search, enter, …). Override per action."""
        return []

    def bug_report_snapshot(self) -> dict:
        """Data needed to re-fetch what this screen is showing (not UX state)."""
        return {
            "kind": "screen",
            "screen": type(self).__name__,
        }

    def bug_report_summary(self) -> str:
        """One-line markup summary shown in the bug-report panel."""
        snap = self.bug_report_snapshot()
        kind = snap.get("kind") or "screen"
        return f"[dim]Capturing[/] [bold]{kind}[/] [dim]replication data[/]"

    def refresh_footer(self) -> None:
        """Rebuild the footer from status + action keys + shared chrome."""
        parts: list[str] = []
        if self.FOOTER_SHOW_HOME:
            parts.append("[bold]H[/bold] home")
        parts.extend(self.footer_status())
        parts.extend(self.footer_action_keys())
        parts.extend(self.FOOTER_CHROME)
        try:
            self.query_one("#help-hint", Label).update(" // ".join(parts))
        except Exception:  # noqa: BLE001
            pass
        self.refresh_bug_report_chrome()

    def refresh_bug_report_chrome(self) -> None:
        """Sync the footer bug hint / open class with panel state."""
        try:
            panel = self.query_one("#bug-report-panel", BugReportPanel)
            hint = self.query_one("#bug-report-hint", Label)
            footer = self.query_one("#shell-footer")
            if panel.is_open:
                hint.update("Enter save · Esc cancel")
                footer.add_class("-bug-open")
            else:
                hint.update("[Report issue]  !")
                footer.remove_class("-bug-open")
        except Exception:  # noqa: BLE001
            pass

    def open_bug_report(
        self,
        *,
        context_summary: str,
        replication: dict,
        screen_name: str,
    ) -> None:
        """Expand the footer bug form under the main pane (keeps context)."""
        panel = self.query_one("#bug-report-panel", BugReportPanel)
        panel.open_panel(
            context_summary=context_summary,
            replication=replication,
            screen_name=screen_name,
        )
        self.refresh_footer()

    def cancel_bug_report(self) -> bool:
        """Collapse the bug panel if open. Return True if it was open."""
        try:
            panel = self.query_one("#bug-report-panel", BugReportPanel)
        except Exception:  # noqa: BLE001
            return False
        if not panel.is_open:
            return False
        panel.close_panel()
        self.refresh_footer()
        return True

    def action_cancel_bug_report(self) -> None:
        """Back-compat alias — prefer :meth:`action_shell_escape`."""
        self.action_shell_escape()

    def action_shell_escape(self) -> None:
        """Esc: close bug form → cancel header rename / tab/Q/w jump → host back."""
        if self.cancel_bug_report():
            return
        try:
            header = self.query_one("#shell-header", ProjectHeader)
        except Exception:  # noqa: BLE001
            header = None
        if header is not None and getattr(header, "_editing", False):
            header.cancel_edit()
            return
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
        except Exception:  # noqa: BLE001
            pane = None
        if pane is not None and pane.cancel_jump():
            return
        try:
            sidebar = self.query_one("#shell-sidebar", ResearchSidebar)
        except Exception:  # noqa: BLE001
            sidebar = None
        if sidebar is not None and sidebar.cancel_jump():
            return
        fn = getattr(self.app, "action_back", None)
        if callable(fn):
            fn()

    def on_bug_report_panel_submitted(
        self, event: BugReportPanel.Submitted
    ) -> None:
        event.stop()
        panel = self.query_one("#bug-report-panel", BugReportPanel)
        replication = panel.pending_replication
        screen_name = panel.pending_screen_name or type(self).__name__
        # Clear pending after read.
        panel._replication = {}
        panel._screen_name = ""
        self.refresh_footer()
        save = getattr(self.app, "_save_bug_report", None)
        if callable(save):
            save(
                event.message,
                replication=replication,
                screen_name=screen_name,
            )

    def on_bug_report_panel_cancelled(
        self, event: BugReportPanel.Cancelled
    ) -> None:
        event.stop()
        self.refresh_footer()

    def action_shell_search(self) -> None:
        fn = getattr(self.app, "action_universal_search", None)
        if callable(fn):
            fn()

    def action_shell_home(self) -> None:
        fn = getattr(self.app, "action_go_home", None)
        if callable(fn):
            fn()

    def action_shell_feedback(self) -> None:
        fn = getattr(self.app, "action_feedback_prompt", None)
        if callable(fn):
            fn()

    def action_shell_new_tab(self) -> None:
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
            if pane._jumping:
                pane.cancel_jump()
        except Exception:  # noqa: BLE001
            pass
        fn = getattr(self.app, "action_new_investigation_tab", None)
        if callable(fn):
            fn()

    def action_shell_commands(self) -> None:
        fn = getattr(self.app, "action_command_palette", None)
        if callable(fn):
            fn()

    def action_shell_tab_jump(self) -> None:
        try:
            sidebar = self.query_one("#shell-sidebar", ResearchSidebar)
            sidebar.cancel_jump()
        except Exception:  # noqa: BLE001
            pass
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
        except Exception:  # noqa: BLE001
            pane = None
        if pane is None:
            return
        pane.start_jump()
        n = len(pane._jump_targets)
        bits = []
        if n:
            bits.append(f"1–{min(n, 9)} switch")
        bits.append("N new")
        if n > 1:
            bits.append("X close")
        bits.append("Esc cancels")
        self.notify("Tabs: " + " · ".join(bits), timeout=4)

    def focus_in_shell_sidebar(self) -> bool:
        """True when focus is inside the research sidebar (queries / workspace)."""
        focused = self.focused
        if focused is None or not self.SHELL_ENABLED:
            return False
        try:
            sidebar = self.query_one("#shell-sidebar", ResearchSidebar)
        except Exception:  # noqa: BLE001
            return False
        node = focused
        while node is not None:
            if node is sidebar:
                return True
            node = node.parent
        return False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Don't steal digits while typing — Esc still clears jump / bug / back.
        if isinstance(self.focused, Input) and action not in {
            "shell_escape",
            "cancel_bug_report",
        }:
            if action.startswith("sidebar_") or action in {
                "focus_pane_main",
                "shell_remove",
                "shell_home",
                "shell_search",
                "shell_new_tab",
                "shell_tab_jump",
            }:
                return False
        return True

    def action_shell_remove(self) -> None:
        """Shift+X — in tab menu: close tab; else remove query/resource."""
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
        except Exception:  # noqa: BLE001
            pane = None
        if pane is not None and pane._jumping:
            pane.cancel_jump()
            close = getattr(self.app, "close_investigation_tab", None)
            if callable(close):
                close()
            return
        fn = getattr(self.app, "remove_active_sidebar_target", None)
        if callable(fn):
            fn()

    def _ensure_sidebar_open(self) -> ResearchSidebar | None:
        try:
            sidebar = self.query_one("#shell-sidebar", ResearchSidebar)
        except Exception:  # noqa: BLE001
            return None
        if sidebar.sidebar_collapsed:
            sidebar.sidebar_collapsed = False
        return sidebar

    def _sidebar_digit(self, digit: int) -> None:
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
        except Exception:  # noqa: BLE001
            pane = None
        if pane is not None and pane.handle_digit(digit):
            return
        sidebar = self._ensure_sidebar_open()
        if sidebar is None:
            return
        sidebar.handle_digit(digit)

    def action_sidebar_jump_queries(self) -> None:
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
            pane.cancel_jump()
        except Exception:  # noqa: BLE001
            pass
        sidebar = self._ensure_sidebar_open()
        if sidebar is not None:
            sidebar.start_jump("queries")

    def action_sidebar_jump_workspace(self) -> None:
        try:
            pane = self.query_one("#shell-pane-bar", PaneBar)
            pane.cancel_jump()
        except Exception:  # noqa: BLE001
            pass
        sidebar = self._ensure_sidebar_open()
        if sidebar is not None:
            sidebar.start_jump("workspace")

    def action_sidebar_digit_0(self) -> None:
        self._sidebar_digit(0)

    def action_sidebar_digit_1(self) -> None:
        self._sidebar_digit(1)

    def action_sidebar_digit_2(self) -> None:
        self._sidebar_digit(2)

    def action_sidebar_digit_3(self) -> None:
        self._sidebar_digit(3)

    def action_sidebar_digit_4(self) -> None:
        self._sidebar_digit(4)

    def action_sidebar_digit_5(self) -> None:
        self._sidebar_digit(5)

    def action_sidebar_digit_6(self) -> None:
        self._sidebar_digit(6)

    def action_sidebar_digit_7(self) -> None:
        self._sidebar_digit(7)

    def action_sidebar_digit_8(self) -> None:
        self._sidebar_digit(8)

    def action_sidebar_digit_9(self) -> None:
        self._sidebar_digit(9)

    def action_focus_pane_queries(self) -> None:
        """Enter query jump mode (replaces old focus-list behavior)."""
        self.action_sidebar_jump_queries()

    def action_focus_pane_workspace(self) -> None:
        """Enter workspace jump mode (replaces old focus-tree behavior)."""
        self.action_sidebar_jump_workspace()

    def action_focus_pane_main(self) -> None:
        body = self.query("#shell-body")
        if not body:
            return
        for child in body[0].walk_children():
            if child.id in {"shell-pane-bar", "shell-tabs", "pane-actions"}:
                continue
            if getattr(child, "focusable", False):
                child.focus()
                return

    # Back-compat aliases used by older help text / docs.
    def action_focus_sidebar(self) -> None:
        self.action_focus_pane_queries()

    def action_focus_main(self) -> None:
        self.action_focus_pane_main()

    def action_toggle_sidebar(self) -> None:
        try:
            sidebar = self.query_one("#shell-sidebar", ResearchSidebar)
            sidebar.sidebar_collapsed = not sidebar.sidebar_collapsed
        except Exception:  # noqa: BLE001
            pass

    def on_project_header_rename_committed(
        self, event: ProjectHeader.RenameCommitted
    ) -> None:
        event.stop()
        fn = getattr(self.app, "_apply_rename", None)
        if callable(fn):
            fn(title=event.title, notes=None)

    def on_research_sidebar_query_selected(
        self, event: ResearchSidebar.QuerySelected
    ) -> None:
        fn = getattr(self.app, "open_query_view", None)
        if callable(fn):
            fn(event.query_id)

    def on_research_sidebar_overview_selected(
        self, event: ResearchSidebar.OverviewSelected
    ) -> None:
        event.stop()
        self.action_shell_home()

    def on_research_sidebar_query_menu(
        self, event: ResearchSidebar.QueryMenu
    ) -> None:
        event.stop()
        fn = getattr(self.app, "open_query_context_menu", None)
        if callable(fn):
            fn(event.query_id, label=event.label)

    def on_research_sidebar_new_query(
        self, event: ResearchSidebar.NewQuery
    ) -> None:
        event.stop()
        fn = getattr(self.app, "action_new_draft_query", None)
        if callable(fn):
            fn()

    def on_research_sidebar_resource_selected(
        self, event: ResearchSidebar.ResourceSelected
    ) -> None:
        fn = getattr(self.app, "open_resource_view", None)
        if callable(fn):
            fn(event.resource_type, event.resource_id)

    def on_research_sidebar_query_removed(
        self, event: ResearchSidebar.QueryRemoved
    ) -> None:
        fn = getattr(self.app, "delete_query", None)
        if callable(fn):
            fn(event.query_id, confirmed=True)

    def on_research_sidebar_resource_removed(
        self, event: ResearchSidebar.ResourceRemoved
    ) -> None:
        fn = getattr(self.app, "remove_workspace_resource", None)
        if callable(fn):
            fn(event.resource_type, event.resource_id, confirmed=True)

    def on_research_sidebar_query_add_resource(
        self, event: ResearchSidebar.QueryAddResource
    ) -> None:
        event.stop()
        fn = getattr(self.app, "query_compose_add_resource", None)
        if callable(fn):
            fn(event.kind, event.query_id)

    def on_research_sidebar_query_remove_resource(
        self, event: ResearchSidebar.QueryRemoveResource
    ) -> None:
        event.stop()
        fn = getattr(self.app, "query_compose_remove_resource", None)
        if callable(fn):
            fn(event.kind, event.resource_id, event.query_id)

    def on_pane_bar_home(self, event: PaneBar.Home) -> None:
        event.stop()
        self.action_shell_home()

    def on_pane_bar_search(self, event: PaneBar.Search) -> None:
        event.stop()
        self.action_shell_search()

    def on_pane_bar_commands(self, event: PaneBar.Commands) -> None:
        event.stop()
        self.action_shell_commands()

    def on_pane_bar_new_tab(self, event: PaneBar.NewTab) -> None:
        event.stop()
        self.action_shell_new_tab()

    def on_pane_bar_tab_selected(self, event: PaneBar.TabSelected) -> None:
        event.stop()
        fn = getattr(self.app, "switch_investigation_tab", None)
        if callable(fn):
            fn(event.investigation_id)

    def on_pane_bar_tab_close(self, event: PaneBar.TabClose) -> None:
        event.stop()
        fn = getattr(self.app, "close_investigation_tab", None)
        if callable(fn):
            fn(event.investigation_id)
