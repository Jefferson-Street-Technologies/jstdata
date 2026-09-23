"""Home action — welcoming root screen for investigations."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, Static

from ..client import JSTDataClient
from ..session import Session
from ..workflows.base import StepBinding, StepSpec, register


class HomeScreen(Screen):
    """Simple text root for an investigation."""

    BINDINGS = [
        Binding("escape", "app.bell", " ", show=False),
    ]

    DEFAULT_CSS = """
    HomeScreen {
        background: #0a0a0a;
    }

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

    #home-meta {
        color: #888;
        margin-top: 2;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        session: Session,
        *,
        title: str = "Investigation",
        body: str = "",
        meta: str = "",
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.session = session
        self.home_title = title
        self.body = body
        self.meta = meta

    def compose(self) -> ComposeResult:
        with Vertical(id="home-body"):
            yield Label("JEFFERSON STREET", id="home-kicker")
            yield Label(self.home_title, id="home-title")
            with VerticalScroll():
                yield Static(self.body or "(no description)", id="home-copy")
            if self.meta:
                yield Label(self.meta, id="home-meta")


def create_home_screen(client, session, **kwargs):
    """Factory used by InvestigationHost for ``type: home`` actions.

    Extra kwargs from the host (not StepArgument seeds):
    ``title``, ``body``, ``meta``.
    """
    title = kwargs.pop("title", "Investigation")
    body = kwargs.pop("body", "")
    meta = kwargs.pop("meta", "")
    return HomeScreen(
        client,
        session,
        title=title,
        body=body,
        meta=meta,
    )


HOME = register(
    StepSpec(
        id="home",
        name="Home",
        description="Investigation welcome / root screen.",
        create_screen=create_home_screen,
        arguments=(),
        bindings=(
            StepBinding("n / p", "navigate", "Next / previous action in BFS order"),
        ),
        example="jst investigation load tutorial",
    )
)
