"""Home action — welcoming root screen for investigations."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, Static

from ..client import JSTDataClient
from ..investigation.model import Workspace
from ..actions.base import ActionBinding, ActionSpec, register
from ..actions.screen import BaseScreen


class HomeScreen(BaseScreen):
    """Simple text root for an investigation."""

    BINDINGS = [
        Binding("escape", "app.bell", " ", show=False),
    ]

    DEFAULT_CSS = """
    #home-body {
        padding: 2 4;
        height: 1fr;
    }

    #home-kicker {
        color: #4ade80;
        text-style: bold;
        margin-bottom: 1;
    }

    #home-title {
        color: #e0e0e0;
        text-style: bold;
        text-align: left;
        margin-bottom: 1;
    }

    #home-copy {
        color: #c0c0c0;
        height: auto;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        title: str = "Investigation",
        body: str = "",
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.home_title = title
        self.body = body

    def compose_body(self) -> ComposeResult:
        with Vertical(id="home-body"):
            yield Label("JEFFERSON STREET", id="home-kicker")
            yield Label(self.home_title, id="home-title")
            with VerticalScroll():
                yield Static(self.body or "(no description)", id="home-copy")

    def footer_status(self) -> list[str]:
        parts = super().footer_status()
        ws = self.workspace
        label = ws.label or f"workspace {ws.id}"
        parts.append(label)
        return parts


def create_home_screen(client, workspace, **kwargs):
    """Factory used by InvestigationHost for ``type: home`` actions.

    Extra kwargs from the host (not ActionArgument seeds):
    ``title``, ``body``.
    """
    title = kwargs.pop("title", "Investigation")
    body = kwargs.pop("body", "")
    kwargs.pop("meta", None)  # legacy; footer is shared via BaseScreen
    return HomeScreen(
        client,
        workspace,
        title=title,
        body=body,
    )


HOME = register(
    ActionSpec(
        id="home",
        name="Home",
        description="Investigation welcome / root screen.",
        create_screen=create_home_screen,
        arguments=(),
        bindings=(
            ActionBinding(
                ":",
                "command",
                "Open command palette (Tab cycle, Space/Enter lock)",
            ),
            ActionBinding(
                "n / p · ↑/↓",
                "siblings",
                "Next / previous sibling action",
            ),
            ActionBinding(
                "N / P · →/←",
                "depth",
                "Into first child / up to parent",
            ),
            ActionBinding(
                "dd",
                "delete_action",
                "Delete this action and all children (no confirm)",
            ),
        ),
        example="jst open tutorial",
    )
)
