"""Interactive project picker for ``jst ls``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from .project_store import list_projects, load_project


@dataclass(frozen=True)
class ProjectRowData:
    project_id: str
    title: str
    inv_count: int
    active: bool


class ProjectRow(ListItem):
    def __init__(self, data: ProjectRowData) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        mark = "* " if self.data.active else "  "
        detail = (
            f"   {self.data.project_id}"
            + (
                f"  ·  {self.data.inv_count} investigation"
                f"{'' if self.data.inv_count == 1 else 's'}"
            )
        )
        yield Label(
            f"{mark}{self.data.title}",
            classes="proj-title",
            markup=False,
        )
        yield Label(detail, classes="proj-detail", markup=False)


class ProjectPickerApp(App[Optional[str]]):
    """List local projects; Enter returns the selected project id."""

    TITLE = "jst projects"
    CSS = """
    Screen {
        background: #0c0c0c;
    }

    #picker-body {
        padding: 1 2;
    }

    #picker-heading {
        text-style: bold;
        color: #e5e5e5;
        margin-bottom: 1;
    }

    #picker-empty {
        color: #888;
    }

    #picker-list {
        height: 1fr;
        background: #0c0c0c;
    }

    ProjectPickerApp ListView > ProjectRow {
        height: 3;
        padding: 0 1;
    }

    ListView > ProjectRow.--highlight {
        background: #1a2a3a;
    }

    .proj-title {
        color: #f5f5f5;
        text-style: bold;
    }

    .proj-detail {
        color: #888;
    }

    #picker-hint {
        color: #666;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("escape", "quit", "Quit", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "open_selected", "Open", show=True),
    ]

    def __init__(
        self,
        rows: list[ProjectRowData],
        *,
        active_project: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.active_project = active_project

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="picker-body"):
            yield Label("PROJECTS", id="picker-heading")
            if not self.rows:
                yield Static(
                    "No projects yet. Create one with `jst new`.",
                    id="picker-empty",
                    markup=False,
                )
            else:
                yield ListView(
                    *[ProjectRow(row) for row in self.rows],
                    id="picker-list",
                )
                yield Label(
                    "enter open · j/k move · q quit",
                    id="picker-hint",
                )
        yield Footer()

    def on_mount(self) -> None:
        if self.rows:
            lst = self.query_one("#picker-list", ListView)
            lst.focus()
            if self.active_project:
                for i, row in enumerate(self.rows):
                    if row.project_id == self.active_project:
                        lst.index = i
                        break

    @on(ListView.Selected, "#picker-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ProjectRow):
            self.exit(event.item.data.project_id)

    def action_open_selected(self) -> None:
        if not self.rows:
            self.exit(None)
            return
        lst = self.query_one("#picker-list", ListView)
        item = lst.highlighted_child
        if isinstance(item, ProjectRow):
            self.exit(item.data.project_id)

    def action_cursor_down(self) -> None:
        if self.rows:
            self.query_one("#picker-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.rows:
            self.query_one("#picker-list", ListView).action_cursor_up()

    def action_quit(self) -> None:
        self.exit(None)


def load_project_rows(
    *,
    app_dir: Optional[Path] = None,
    active_project: Optional[str] = None,
) -> list[ProjectRowData]:
    rows: list[ProjectRowData] = []
    for pid in list_projects(app_dir=app_dir):
        try:
            project = load_project(pid, app_dir=app_dir)
            title = project.title
            inv_count = len(project.investigations)
        except Exception:
            title = pid
            inv_count = 0
        rows.append(
            ProjectRowData(
                project_id=pid,
                title=title,
                inv_count=inv_count,
                active=pid == active_project,
            )
        )
    return rows


def run_project_picker(
    *,
    app_dir: Optional[Path] = None,
    active_project: Optional[str] = None,
) -> Optional[str]:
    """Open the picker TUI; return selected project id or None if cancelled."""
    rows = load_project_rows(app_dir=app_dir, active_project=active_project)
    app = ProjectPickerApp(rows, active_project=active_project)
    return app.run()
