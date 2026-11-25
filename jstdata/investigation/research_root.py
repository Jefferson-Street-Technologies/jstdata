"""Research home — investigation landing when no query chart is open."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, Static

from ..actions.screen import BaseScreen
from ..client import JSTDataClient
from .model import Workspace
from .ui_primitives import ActionButton


class ResearchRootScreen(BaseScreen):
    """Investigation home: continue research, recent queries, notes."""

    BINDINGS = [
        Binding("slash", "shell_search", "Search", show=False),
        Binding("enter", "shell_search", "Search", show=False),
        Binding("n", "create_query", "Create query", show=False),
        Binding("r", "resume_last", "Resume last query", show=False),
    ]

    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]/[/bold] search",
        "[bold]n[/bold] create query",
        "[bold]r[/bold] resume",
        "[bold]Ctrl+o[/bold] back",
    )

    DEFAULT_CSS = """
    #root-body {
        height: 1fr;
        padding: 1 3;
    }
    #root-title {
        color: #f5f5f5;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #root-rule {
        color: #333;
        height: 1;
        margin-bottom: 1;
    }
    #root-desc {
        color: #a8a8a8;
        height: auto;
        max-height: 4;
        margin-bottom: 1;
    }
    #root-home-actions {
        height: 1;
        margin-bottom: 1;
    }
    #root-stats {
        color: #888;
        height: 1;
        margin-bottom: 1;
    }
    #root-section, .root-kicker {
        color: #86efac;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #root-ctas {
        height: 3;
        margin: 1 0;
    }
    #root-recent {
        height: auto;
        max-height: 10;
        color: #e0e0e0;
        margin-bottom: 1;
    }
    #root-notes {
        height: auto;
        max-height: 6;
        color: #c0c0c0;
    }
    .recent-line {
        height: 1;
        color: #e0e0e0;
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
        with VerticalScroll(id="root-body"):
            yield Label("", id="root-title")
            yield Label("─" * 54, id="root-rule")
            yield Static("", id="root-desc", markup=True)
            with Horizontal(id="root-home-actions"):
                yield ActionButton(
                    "Edit description",
                    action_id="edit_description",
                    variant="secondary",
                    id="root-edit-desc",
                )
                yield ActionButton(
                    "Publish",
                    action_id="publish",
                    variant="primary",
                    id="root-publish",
                )
            yield Label("", id="root-stats")
            yield Label("CONTINUE RESEARCH", id="root-section")
            with Horizontal(id="root-ctas"):
                yield ActionButton(
                    "Search data catalog",
                    action_id="search",
                    shortcut="/",
                    variant="secondary",
                )
                yield ActionButton(
                    "Create query",
                    action_id="create_query",
                    shortcut="n",
                    variant="secondary",
                )
                yield ActionButton(
                    "Resume last query",
                    action_id="resume",
                    shortcut="r",
                    variant="ghost",
                )
            yield Label("RECENT QUERIES", id="root-recent-kicker", classes="root-kicker")
            yield Static("", id="root-recent", markup=True)
            yield Label("RESEARCH NOTES", id="root-notes-kicker", classes="root-kicker")
            yield Static("", id="root-notes", markup=True)
            yield ActionButton(
                "+ Add note",
                action_id="add_note",
                variant="ghost",
                id="root-add-note",
            )

    def on_mount(self) -> None:
        super().on_mount()
        self.refresh_home()

    def refresh_home(self) -> None:
        inv = getattr(self.app, "investigation", None)
        title = self._title or (inv.title if inv else "Investigation")
        self.query_one("#root-title", Label).update(title)
        desc = (inv.description if inv else None) or self._body or (
            "[dim]No description yet. Add context for this investigation.[/]"
        )
        self.query_one("#root-desc", Static).update(desc)
        ws = self.workspace
        queries = list(inv.queries.values()) if inv else []
        saved = sum(1 for q in queries if q.in_report)
        drafts = sum(1 for q in queries if not q.in_report)
        self.query_one("#root-stats", Label).update(
            f"{saved} saved quer{'y' if saved == 1 else 'ies'}     "
            f"{drafts} draft{'s' if drafts != 1 else ''}     "
            f"{len(ws.entity)} entities     "
            f"{len(ws.metric)} metrics"
        )
        recent_lines: list[str] = []
        for q in sorted(queries, key=lambda x: -x.id)[:6]:
            if q.is_empty() and not q.in_report:
                continue
            name = q.name or f"Query {q.id}"
            n = max(len(q.metric) * len(q.entity), len(q.series), 0)
            series_bit = f"{n} series" if n else "empty"
            mark = "" if q.in_report else " [dim]draft[/]"
            recent_lines.append(f"  {name}{mark}     [dim]{series_bit}[/]")
        self.query_one("#root-recent", Static).update(
            "\n".join(recent_lines) if recent_lines else "[dim]  No queries yet[/]"
        )
        notes = (inv.annotations if inv else None) or ""
        self.query_one("#root-notes", Static).update(
            notes if notes else "[dim]  No research notes yet.[/]"
        )

    def shell_breadcrumb(self) -> str:
        return "Research"

    def footer_status(self) -> list[str]:
        return []

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold #e0e0e0]/[/] [#888]search[/]",
            "[bold #e0e0e0]n[/] [#888]create query[/]",
            "[bold #e0e0e0]r[/] [#888]resume[/]",
        ]

    def action_create_query(self) -> None:
        create = getattr(self.app, "action_new_draft_query", None)
        if callable(create):
            create()

    def action_resume_last(self) -> None:
        preferred = getattr(self.app, "_preferred_query_id", None)
        open_fn = getattr(self.app, "open_query_view", None)
        if callable(preferred) and callable(open_fn):
            qid = preferred()
            if qid is not None:
                open_fn(qid)
                return
        self.notify("No query to resume", severity="warning")

    @on(ActionButton.Pressed)
    def _button(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "search":
            self.action_shell_search()
        elif event.button_id == "create_query":
            self.action_create_query()
        elif event.button_id == "resume":
            self.action_resume_last()
        elif event.button_id == "publish":
            pub = getattr(self.app, "action_publish", None) or getattr(
                self.app, "_cmd_publish", None
            )
            if callable(pub):
                pub()
            else:
                self.notify("Use :publish to publish this investigation")
        elif event.button_id == "edit_description":
            edit = getattr(self.app, "edit_investigation_description", None)
            if callable(edit):
                edit()
            else:
                self.notify("Use :rename or investigation settings to edit description")
        elif event.button_id == "add_note":
            note = getattr(self.app, "edit_investigation_annotations", None)
            if callable(note):
                note()
            else:
                self.notify("Open a query and press a to annotate")


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
