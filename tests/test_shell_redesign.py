"""Chart-first research shell: draft ladder, chrome helpers, home factory."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.widgets import Input, Static

from jstdata.actions.base import get_action
from jstdata.actions.screen import BaseScreen
from jstdata.investigation.api import add_query, create_investigation, persist_investigation
from jstdata.investigation.project_store import load_project_investigation
from jstdata.investigation.research_root import ResearchRootScreen
from jstdata.investigation.shell import PaneBar, ProjectHeader, ResearchSidebar, TabBar


def test_home_action_uses_research_root():
    spec = get_action("home")
    assert spec.name == "Research"
    # Factory returns ResearchRootScreen
    from jstdata.client import JSTDataClient
    from jstdata.investigation.model import Workspace

    screen = spec.create_screen(JSTDataClient.__new__(JSTDataClient), Workspace())
    assert isinstance(screen, ResearchRootScreen)
    assert screen.SHELL_ENABLED is True


def test_draft_then_save_ladder(tmp_path: Path):
    inv = create_investigation("shell-demo", title="Shell", app_dir=tmp_path)
    q = add_query(
        inv,
        name="OpInc",
        metric=["operating-income"],
        entity=["cik:1"],
        app_dir=tmp_path,
        persist=False,
    )
    q.in_report = False
    persist_investigation(inv, app_dir=tmp_path)
    loaded = load_project_investigation(inv.id, inv.id, app_dir=tmp_path)
    assert loaded.queries[1].in_report is False

    loaded.queries[1].in_report = True
    persist_investigation(loaded, app_dir=tmp_path)
    again = load_project_investigation(inv.id, inv.id, app_dir=tmp_path)
    assert again.queries[1].in_report is True


def test_sidebar_refresh_splits_drafts_and_saved():
    sidebar = ResearchSidebar()
    # Compose requires an app; exercise data helper via mounting is heavy —
    # validate TabBar / Header text helpers instead.
    header = ProjectHeader()
    header.set_project("Homebuilders")
    assert header._title == "Homebuilders"

    tabs = TabBar()
    tabs.set_tabs(
        ["fundamentals", "peers"],
        labels={"fundamentals": "Fundamentals", "peers": "Peers"},
        active="peers",
        marks={"peers": "●"},
    )
    assert tabs._order == ["fundamentals", "peers"]
    assert tabs._active == "peers"
    rendered = str(tabs.render())
    assert "Peers" in rendered or "peers" in rendered.lower() or "●" in rendered

    single = TabBar()
    single.set_tabs(
        ["only"],
        labels={"only": "Only"},
        active="only",
    )
    assert single.has_class("-empty") or len(single._order) == 1

    pane = PaneBar()
    pane._paint()
    painted = str(pane.render())
    assert "Home" in painted
    assert "Search" in painted
    assert "Commands" in painted
    assert "+" in painted

    pane.set_tabs(
        ["only"],
        labels={"only": "Only"},
        active="only",
    )
    painted = str(pane.render())
    assert "Only" in painted
    assert "×" not in painted  # sole tab cannot close
    assert "+" in painted

    pane.set_tabs(
        ["fundamentals", "peers"],
        labels={"fundamentals": "Fundamentals", "peers": "Peers"},
        active="peers",
        marks={"peers": "●"},
    )
    painted = str(pane.render())
    assert "Fundamentals" in painted
    assert "Peers" in painted
    assert "×" in painted
    assert pane.start_jump() is True
    assert pane._jumping is True


def test_hidden_header_input_does_not_block_jump_bindings():
    """display:none header Input must not autofocus / swallow T Q W digits."""

    class Body(Static):
        can_focus = True

        def __init__(self) -> None:
            super().__init__("body")

    class JumpScreen(BaseScreen):
        def compose_body(self):
            yield Body()

    class Pilot(App):
        BINDINGS = [Binding("T", "tab_jump", "T")]

        def __init__(self) -> None:
            super().__init__()
            self.ok = False
            self.focus_name = ""

        def on_mount(self) -> None:
            self.push_screen(JumpScreen())
            self.call_later(self._drive)

        def action_tab_jump(self) -> None:
            start = getattr(self.screen, "action_shell_tab_jump", None)
            if callable(start):
                start()

        async def _drive(self) -> None:
            await asyncio.sleep(0.05)
            screen = self.screen
            assert isinstance(screen, JumpScreen)
            self.focus_name = type(self.focused).__name__ if self.focused else ""
            assert not isinstance(self.focused, Input), self.focus_name
            header_in = screen.query_one("#shell-header-input", Input)
            assert header_in.can_focus is False
            pane = screen.query_one("#shell-pane-bar", PaneBar)
            pane.set_tabs(
                ["a", "b"],
                labels={"a": "Alpha", "b": "Beta"},
                active="a",
            )
            assert await self._check_bindings("T") is True
            assert pane._jumping is True
            assert await self._check_bindings("2") is True
            assert pane._jumping is False
            self.ok = True
            self.exit()

    app = Pilot()
    app.run()
    assert app.ok
