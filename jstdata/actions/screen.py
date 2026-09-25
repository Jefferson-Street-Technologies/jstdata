"""Shared base screen for investigation actions (fixed chrome footer)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Label


class BaseScreen(Screen):
    """Action screen with a shared bottom footer for investigation chrome.

    Subclasses implement :meth:`compose_body` (and optionally
    :meth:`footer_status` / :meth:`footer_action_keys`). Drawers and modals
    stay standalone and do not inherit this.
    """

    # Investigation-wide keys — defined once, shown on every action screen.
    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]:[/bold] commands",
        "[bold]n[/bold]/[bold]p[/bold] siblings",
        "[bold]N[/bold]/[bold]P[/bold] depth",
        "[bold]dd[/bold] delete",
        "[bold]?[/bold] help",
        "[bold]q[/bold] quit",
    )

    DEFAULT_CSS = """
    BaseScreen {
        background: #0a0a0a;
    }

    #cmd-bar {
        height: 3;
        background: #0f0f0f;
        border-top: solid #4ade80;
        align: left middle;
        padding: 0 1;
    }

    #help-hint {
        color: #888;
        width: 1fr;
        overflow: hidden;
    }
    """

    def compose(self) -> ComposeResult:
        yield from self.compose_body()
        with Horizontal(id="cmd-bar"):
            yield Label("", id="help-hint", markup=True)

    def compose_body(self) -> ComposeResult:
        """Yield the screen body above the shared footer."""
        yield from ()

    def on_mount(self) -> None:
        self.refresh_footer()

    def footer_status(self) -> list[str]:
        """Leading status chips (action id, scope, …)."""
        n = getattr(self.app, "action_count", 1)
        i = getattr(self.app, "action_index", 0) + 1
        spec = getattr(self.app, "current_action_spec", None)
        action_id = getattr(spec, "id", None) or "action"
        return [f"{action_id} {i}/{n}"]

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
        self.query_one("#help-hint", Label).update(" // ".join(parts))
