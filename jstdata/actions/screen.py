"""Shared base screen for investigation actions (research shell chrome)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, ListView, Tree

from ..investigation.shell import ProjectHeader, ResearchSidebar, TabBar


class BaseScreen(Screen):
    """Action screen with persistent research shell chrome.

    Layout: project header → tab bar → (sidebar | body) → footer.
    Subclasses implement :meth:`compose_body`. Drawers/modals stay standalone.

    Pane focus: ``1`` queries · ``2`` workspace · ``3`` main.
    """

    # Investigation-wide keys — defined once, shown on every action screen.
    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]1[/bold]/[bold]2[/bold]/[bold]3[/bold] panes",
        "[bold]/[/bold] search",
        "[bold]:[/bold] commands",
        "[bold]n[/bold] query",
        "[bold]e[/bold] edit",
        "[bold]s[/bold] save",
        "[bold]a[/bold] annotate",
        "[bold]H[/bold] history",
        "[bold]F[/bold] feedback",
        "[bold]{[/bold]/[bold]}[/bold] tabs",
        "[bold]?[/bold] help",
        "[bold]q[/bold] quit",
    )

    # Shell-level bindings (host also owns some; these help when focus is here).
    BINDINGS = [
        Binding("slash", "shell_search", "Search", show=False),
        Binding("H", "shell_history", "History", show=False),
        Binding("F", "shell_feedback", "Feedback", show=False),
        Binding("n", "shell_new_query", "New query", show=False),
        Binding("1", "focus_pane_queries", "Queries", show=False),
        Binding("2", "focus_pane_workspace", "Workspace", show=False),
        Binding("3", "focus_pane_main", "Main", show=False),
        Binding("ctrl+h", "focus_pane_queries", "Focus queries", show=False),
        Binding("ctrl+l", "focus_pane_main", "Focus main", show=False),
        Binding("backslash", "toggle_sidebar", "Toggle sidebar", show=False),
    ]

    DEFAULT_CSS = """
    BaseScreen {
        background: #0a0a0a;
        layout: vertical;
    }

    #shell-header {
        height: 1;
        dock: top;
    }

    #shell-tabs {
        height: 1;
        dock: top;
    }

    #shell-main {
        height: 1fr;
        width: 100%;
    }

    #shell-body {
        width: 1fr;
        height: 1fr;
    }

    #shell-main-kicker {
        height: 1;
        color: #4ade80;
        text-style: bold;
        padding: 0 1;
        background: #0c0c0c;
    }

    #cmd-bar {
        height: 3;
        background: #0f0f0f;
        border-top: solid #4ade80;
        align: left middle;
        padding: 0 1;
        dock: bottom;
    }

    #help-hint {
        color: #888;
        width: 1fr;
        overflow: hidden;
    }
    """

    # Screens that should not wrap body in the research shell chrome.
    SHELL_ENABLED: bool = True

    def compose(self) -> ComposeResult:
        if self.SHELL_ENABLED:
            yield ProjectHeader(id="shell-header")
            yield TabBar(id="shell-tabs")
            with Horizontal(id="shell-main"):
                yield ResearchSidebar(id="shell-sidebar")
                with Vertical(id="shell-body"):
                    yield Label(
                        "[bold #86efac]3[/] MAIN",
                        id="shell-main-kicker",
                        markup=True,
                    )
                    yield from self.compose_body()
        else:
            yield from self.compose_body()
        with Horizontal(id="cmd-bar"):
            yield Label("", id="help-hint", markup=True)

    def compose_body(self) -> ComposeResult:
        """Yield the screen body above the shared footer."""
        yield from ()

    def on_mount(self) -> None:
        self.refresh_footer()
        self.refresh_shell_chrome()

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
        """Leading status chips (action id, scope, …). Tabs live in TabBar."""
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

    def refresh_footer(self) -> None:
        """Rebuild the footer from status + action keys + shared chrome."""
        parts = [
            *self.footer_status(),
            *self.footer_action_keys(),
            *self.FOOTER_CHROME,
        ]
        try:
            self.query_one("#help-hint", Label).update(" // ".join(parts))
        except Exception:  # noqa: BLE001
            pass

    def action_shell_search(self) -> None:
        fn = getattr(self.app, "action_universal_search", None)
        if callable(fn):
            fn()

    def action_shell_history(self) -> None:
        fn = getattr(self.app, "action_show_activity", None)
        if callable(fn):
            fn()

    def action_shell_feedback(self) -> None:
        fn = getattr(self.app, "action_feedback_prompt", None)
        if callable(fn):
            fn()

    def action_shell_new_query(self) -> None:
        fn = getattr(self.app, "action_new_draft_query", None)
        if callable(fn):
            fn()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Don't steal digits while typing in an input.
        if isinstance(self.focused, Input) and action in {
            "focus_pane_queries",
            "focus_pane_workspace",
            "focus_pane_main",
        }:
            return False
        return True

    def _ensure_sidebar_open(self) -> ResearchSidebar | None:
        try:
            sidebar = self.query_one("#shell-sidebar", ResearchSidebar)
        except Exception:  # noqa: BLE001
            return None
        if sidebar.sidebar_collapsed:
            sidebar.sidebar_collapsed = False
        return sidebar

    def action_focus_pane_queries(self) -> None:
        if self._ensure_sidebar_open() is None:
            return
        try:
            self.query_one("#sidebar-query-list", ListView).focus()
        except Exception:  # noqa: BLE001
            pass

    def action_focus_pane_workspace(self) -> None:
        if self._ensure_sidebar_open() is None:
            return
        try:
            self.query_one("#sidebar-ws-tree", Tree).focus()
        except Exception:  # noqa: BLE001
            pass

    def action_focus_pane_main(self) -> None:
        body = self.query("#shell-body")
        if not body:
            return
        for child in body[0].walk_children():
            if child.id == "shell-main-kicker":
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

    def on_research_sidebar_query_selected(
        self, event: ResearchSidebar.QuerySelected
    ) -> None:
        fn = getattr(self.app, "open_query_view", None)
        if callable(fn):
            fn(event.query_id)

    def on_research_sidebar_resource_selected(
        self, event: ResearchSidebar.ResourceSelected
    ) -> None:
        fn = getattr(self.app, "open_resource_view", None)
        if callable(fn):
            fn(event.resource_type, event.resource_id)

    def on_research_sidebar_new_query(
        self, event: ResearchSidebar.NewQuery
    ) -> None:
        fn = getattr(self.app, "action_new_draft_query", None)
        if callable(fn):
            fn()
