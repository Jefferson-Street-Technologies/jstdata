"""Empty research root — chart-first landing when no query is open."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label, Static

from ..actions.screen import BaseScreen
from ..client import JSTDataClient
from .model import Workspace


class ResearchRootScreen(BaseScreen):
    """Quiet landing: point users at search, new query, and the sidebar."""

    BINDINGS = [
        Binding("slash", "shell_search", "Search", show=False),
        Binding("n", "shell_new_query", "New query", show=False),
        Binding("enter", "shell_search", "Search", show=False),
    ]

    FOOTER_CHROME: tuple[str, ...] = BaseScreen.FOOTER_CHROME

    DEFAULT_CSS = """
    #root-body {
        height: 1fr;
        padding: 3 4;
        align: left middle;
    }
    #root-title {
        color: #e0e0e0;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #root-hint {
        color: #888;
        height: auto;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        title: str = "",
        body: str = "",
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._title = title
        self._body = body

    def compose_body(self) -> ComposeResult:
        with Vertical(id="root-body"):
            yield Label("Research", id="root-title")
            yield Static(
                "Press [bold]/[/bold] to search · [bold]n[/bold] new query · "
                "pick a query from the sidebar\n"
                "[bold]1[/bold]/[bold]2[/bold]/[bold]3[/bold] panes · "
                "[bold]H[/bold] history · [bold]F[/bold] feedback · "
                "[bold]\\[/bold] toggle sidebar",
                id="root-hint",
                markup=True,
            )

    def shell_breadcrumb(self) -> str:
        return "Research"

    def footer_status(self) -> list[str]:
        return []

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold #e0e0e0]/[/] [#888]search[/]",
            "[bold #e0e0e0]n[/] [#888]new query[/]",
            "[bold #e0e0e0]1[/]/[bold #e0e0e0]2[/]/[bold #e0e0e0]3[/] [#888]panes[/]",
        ]


def create_research_root_screen(client, workspace, **kwargs):
    """Factory for ``type: home`` actions (chart-first root)."""
    title = kwargs.pop("title", "Investigation")
    body = kwargs.pop("body", "")
    kwargs.pop("meta", None)
    kwargs.pop("input_context", None)
    return ResearchRootScreen(
        client,
        workspace,
        title=title,
        body=body,
    )
