"""Shared base screen for investigation actions (research shell chrome)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label

from ..investigation.shell import ProjectHeader, ResearchSidebar, TabBar


class BaseScreen(Screen):
    """Action screen with persistent research shell chrome.

    Layout: project header → tab bar → (sidebar | body) → footer.
    Subclasses implement :meth:`compose_body`. Drawers/modals stay standalone.

    Sidebar: click to open. ``1`` / ``2`` enter jump mode for queries /
    workspace; then press the highlighted digit. Esc cancels jump.
    """

    # Investigation-wide keys — shortcuts also appear next to on-screen controls.
    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]/[/bold] search",
        "[bold]1[/bold]/[bold]2[/bold] jump",
        "[bold]n[/bold] query",
        "[bold]a[/bold] annotate",
        "[bold]Ctrl+o[/bold] back",
    )

    # Shell-level bindings (host also owns some; these help when focus is here).
    BINDINGS = [
        Binding("slash", "shell_search", "Search", show=False),
        Binding("H", "shell_history", "History", show=False),
        Binding("F", "shell_feedback", "Feedback", show=False),
        Binding("n", "shell_new_query", "New query", show=False),
        Binding("1", "sidebar_digit_1", "Jump queries / pick 1", show=False),
        Binding("2", "sidebar_digit_2", "Jump workspace / pick 2", show=False),
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
        color: #888;
        padding: 0 1;
        background: #0c0c0c;
        border-bottom: solid #1a1a1a;
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

    #bug-report-hint {
        width: auto;
        color: #fbbf24;
        text-style: bold;
        padding: 0 1;
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
                        "",
                        id="shell-main-kicker",
                        markup=True,
                    )
                    yield from self.compose_body()
        else:
            yield from self.compose_body()
        with Horizontal(id="cmd-bar"):
            yield Label("", id="help-hint", markup=True)
            yield Label(
                "Problems? Press !",
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
        if not self.SHELL_ENABLED:
            return
        try:
            crumb = self.shell_breadcrumb()
            self.query_one("#shell-main-kicker", Label).update(
                f"[bold #86efac]3[/]  {crumb}"
            )
        except Exception:  # noqa: BLE001
            pass

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
        self.refresh_breadcrumb()

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

    def bug_report_snapshot(self) -> dict:
        """Data needed to re-fetch what this screen is showing (not UX state)."""
        return {
            "kind": "screen",
            "screen": type(self).__name__,
        }

    def bug_report_summary(self) -> str:
        """One-line markup summary shown in the bug-report modal."""
        snap = self.bug_report_snapshot()
        kind = snap.get("kind") or "screen"
        return f"[dim]Capturing[/] [bold]{kind}[/] [dim]replication data[/]"

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
        # Don't steal digits / Esc while typing in an input.
        if isinstance(self.focused, Input) and action.startswith("sidebar_"):
            return False
        if isinstance(self.focused, Input) and action in {
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

    def _sidebar_digit(self, digit: int) -> None:
        sidebar = self._ensure_sidebar_open()
        if sidebar is None:
            return
        sidebar.handle_digit(digit)

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
        self._sidebar_digit(1)

    def action_focus_pane_workspace(self) -> None:
        """Enter workspace jump mode (replaces old focus-tree behavior)."""
        self._sidebar_digit(2)

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
