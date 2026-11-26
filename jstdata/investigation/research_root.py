"""Research home — investigation landing when no query chart is open."""

from __future__ import annotations

import asyncio
import re

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..actions.screen import BaseScreen
from ..client import JSTDataClient
from ..models import Release
from .model import Workspace
from .ui_primitives import ActionButton

RELEASES_PAGE = 25
_INGESTED_SUFFIX = re.compile(r"\s*\(ingested\s+\d{4}-\d{2}-\d{2}\)\s*$", re.I)


def _release_display_name(release: Release) -> str:
    raw = (release.label or release.id or "").strip()
    return _INGESTED_SUFFIX.sub("", raw).strip() or release.id


class _ReleaseRow(ListItem):
    def __init__(self, release: Release) -> None:
        self.release = release
        name = _release_display_name(release)
        source = release.source or ""
        date_s = (
            release.release_date.isoformat()
            if getattr(release, "release_date", None)
            else ""
        )
        meta = "  ".join(
            p for p in (source, date_s) if p
        )
        super().__init__(
            Label(
                f"{name}"
                + (f"  [dim]{meta}[/]" if meta else "")
                + "\n",  # blank second line — keeps row height / breathing room
                markup=True,
            )
        )


class ResearchRootScreen(BaseScreen):
    """Overview — report / investigation summary when no query chart is open."""

    BINDINGS = [
        Binding("slash", "shell_search", "Search", show=False),
        Binding("a", "add_note", "Add note", show=False),
        # Report rename is header ✎ or `:rename --title …` — no keyboard chord.
        Binding("D", "edit_description", "Edit description", show=False, priority=True),
        Binding("P", "publish", "Publish", show=False, priority=True),
        Binding("r", "load_releases", "Load releases", show=False),
        # No priority — while the inline title/description Inputs are focused,
        # Enter must reach Input.Submitted, not this binding.
        Binding("enter", "open_release", "Open release", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    FOOTER_SHOW_HOME = False
    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]a[/bold] notes",
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
    #root-title-input {
        color: #f5f5f5;
        text-style: bold;
        height: 3;
        margin-top: 1;
        background: #0a0a0a;
        border: tall #4ade80;
        display: none;
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
    #root-desc-input {
        color: #e0e0e0;
        height: 3;
        margin-bottom: 0;
        background: #0a0a0a;
        border: tall #4ade80;
        display: none;
    }
    #root-edit-hint {
        color: #888;
        height: 1;
        margin-bottom: 1;
        display: none;
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
    #root-report {
        height: auto;
        max-height: 16;
        color: #c0c0c0;
        margin-bottom: 1;
    }
    .root-kicker {
        color: #86efac;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #root-notes {
        height: auto;
        max-height: 8;
        color: #c0c0c0;
        margin-bottom: 1;
    }
    #root-releases-prompt {
        height: auto;
        color: #a3a3a3;
        margin: 1 0;
    }
    #root-releases-status {
        color: #888;
        height: 1;
    }
    #root-releases-list {
        height: 1fr;
        min-height: 8;
        background: #0c0c0c;
        display: none;
    }
    #root-releases-list > ListItem {
        height: auto;
        min-height: 2;
        max-height: 3;
        padding: 0 1;
        background: transparent;
        color: #e0e0e0;
    }
    #root-releases-list > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
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
        self._releases: list[Release] = []
        self._releases_loaded = False
        self._releases_loading = False
        self._editing: str | None = None  # "title" | "description"

    def compose_body(self) -> ComposeResult:
        with Vertical(id="root-body"):
            yield Label("", id="root-title")
            title_in = Input(placeholder="Title", id="root-title-input")
            title_in.can_focus = False
            yield title_in
            yield Label("─" * 54, id="root-rule")
            yield Static("", id="root-desc", markup=True)
            desc_in = Input(placeholder="Description", id="root-desc-input")
            desc_in.can_focus = False
            yield desc_in
            yield Static(
                "[bold]enter[/bold] save  ·  [bold]esc[/bold] cancel",
                id="root-edit-hint",
                markup=True,
            )
            with Horizontal(id="root-home-actions"):
                yield ActionButton(
                    "Edit description",
                    action_id="edit_description",
                    shortcut="D",
                    variant="secondary",
                    id="root-edit-desc",
                )
                yield ActionButton(
                    "Rename",
                    action_id="rename",
                    shortcut="R",
                    variant="secondary",
                    id="root-rename",
                )
                yield ActionButton(
                    "Publish",
                    action_id="publish",
                    shortcut="P",
                    variant="primary",
                    id="root-publish",
                )
            yield Label("", id="root-stats")
            yield Label("REPORT", id="root-report-kicker", classes="root-kicker")
            yield Static("", id="root-report", markup=True)
            yield Label("RESEARCH NOTES", id="root-notes-kicker", classes="root-kicker")
            yield Static("", id="root-notes", markup=True)
            yield ActionButton(
                "+ Add note",
                action_id="add_note",
                shortcut="a",
                variant="secondary",
                id="root-add-note",
            )
            yield Label(
                "RECENT RELEASES", id="root-releases-kicker", classes="root-kicker"
            )
            yield Static(
                "Recent filings and data releases stay idle until you load them.\n"
                "Press [bold]r[/bold] or click Load — results stay on this page.",
                id="root-releases-prompt",
                markup=True,
            )
            yield ActionButton(
                "Load recent releases",
                action_id="load_releases",
                shortcut="r",
                variant="secondary",
                id="root-load-releases",
            )
            yield Static("", id="root-releases-status", markup=True)
            yield ListView(id="root-releases-list")

    def on_mount(self) -> None:
        super().on_mount()
        self.refresh_home()
        # Warm release list from disk cache immediately when present.
        if self._releases_cache_hit():
            self.call_later(self.load_releases)
        pending = getattr(self.app, "_pending_inline_edit", None)
        if pending in ("title", "description"):
            setattr(self.app, "_pending_inline_edit", None)
            focus = pending
            self.call_later(lambda: self.begin_inline_edit(focus=focus))

    def _releases_cache_params(self) -> dict:
        return {
            "limit": RELEASES_PAGE,
            "offset": 0,
            "sort_order": "desc",
        }

    def _releases_cache_hit(self) -> bool:
        from ..client import JSTDataCache

        try:
            return (
                JSTDataCache("/release", self._releases_cache_params()).read()
                is not None
            )
        except Exception:  # noqa: BLE001
            return False

    def begin_inline_edit(self, *, focus: str = "title") -> None:
        """Swap title or description into an inline Input and focus it."""
        if focus not in ("title", "description"):
            focus = "title"
        if self._editing and self._editing != focus:
            self._exit_inline_edit(restore=True)
        inv = getattr(self.app, "investigation", None)
        title_val = (inv.title if inv else None) or self._title or ""
        desc_val = (inv.notes if inv else None) or ""
        title_lbl = self.query_one("#root-title", Label)
        title_in = self.query_one("#root-title-input", Input)
        desc_lbl = self.query_one("#root-desc", Static)
        desc_in = self.query_one("#root-desc-input", Input)
        hint = self.query_one("#root-edit-hint", Static)
        if focus == "title":
            title_in.value = title_val
            title_lbl.display = False
            title_in.can_focus = True
            title_in.display = True
            desc_lbl.display = True
            desc_in.can_focus = False
            desc_in.display = False
            self._editing = "title"
            hint.display = True
            title_in.focus()
            title_in.cursor_position = len(title_in.value)
        else:
            desc_in.value = desc_val
            desc_lbl.display = False
            desc_in.can_focus = True
            desc_in.display = True
            title_lbl.display = True
            title_in.can_focus = False
            title_in.display = False
            self._editing = "description"
            hint.display = True
            desc_in.focus()
            desc_in.cursor_position = len(desc_in.value)

    def _exit_inline_edit(self, *, restore: bool = True) -> None:
        self._editing = None
        try:
            self.query_one("#root-title", Label).display = True
            title_in = self.query_one("#root-title-input", Input)
            title_in.display = False
            title_in.can_focus = False
            self.query_one("#root-desc", Static).display = True
            desc_in = self.query_one("#root-desc-input", Input)
            desc_in.display = False
            desc_in.can_focus = False
            self.query_one("#root-edit-hint", Static).display = False
            if self.focused in (title_in, desc_in):
                self.set_focus(None)
        except Exception:  # noqa: BLE001
            pass
        if restore:
            self.refresh_home()

    def action_rename(self) -> None:
        edit = getattr(self.app, "edit_investigation_description", None)
        if callable(edit):
            edit(focus="title")
        else:
            self.begin_inline_edit(focus="title")

    def action_edit_description(self) -> None:
        edit = getattr(self.app, "edit_investigation_description", None)
        if callable(edit):
            edit(focus="description")
        else:
            self.begin_inline_edit(focus="description")

    def action_publish(self) -> None:
        pub = getattr(self.app, "action_publish", None) or getattr(
            self.app, "_cmd_publish", None
        )
        if callable(pub):
            pub()
        else:
            self.notify("Use :publish to publish this investigation")

    def action_shell_escape(self) -> None:
        if self._editing:
            self._exit_inline_edit(restore=True)
            return
        super().action_shell_escape()

    def _inline_input_focused(self) -> bool:
        focused = self.focused
        return isinstance(focused, Input) and getattr(focused, "id", None) in {
            "root-title-input",
            "root-desc-input",
        }

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Let the inline Inputs own Enter (and other keys) while editing.
        if self._editing or self._inline_input_focused():
            if action in {
                "open_release",
                "rename",
                "edit_description",
                "publish",
                "load_releases",
                "add_note",
                "cursor_down",
                "cursor_up",
                "shell_search",
            }:
                return False
        return super().check_action(action, parameters)

    def _commit_inline_edit(self, value: str) -> None:
        field = self._editing
        if field is None:
            return
        apply = getattr(self.app, "_apply_rename", None)
        self._exit_inline_edit(restore=False)
        if not callable(apply):
            self.refresh_home()
            return
        if field == "title":
            title = value.strip()
            if not title:
                self.notify("Title cannot be empty", severity="warning")
                self.begin_inline_edit(focus="title")
                return
            apply(title=title, notes=None)
        else:
            apply(title=None, notes=value)

    @on(Input.Submitted, "#root-title-input")
    def _title_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._commit_inline_edit(event.value)

    @on(Input.Submitted, "#root-desc-input")
    def _desc_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._commit_inline_edit(event.value)

    def refresh_home(self) -> None:
        if self._editing:
            return
        inv = getattr(self.app, "investigation", None)
        from rich.markup import escape

        title = (inv.title if inv else None) or self._title or "Investigation"
        self.query_one("#root-title", Label).update(escape(title))
        desc = (inv.notes if inv else None) or self._body
        if desc:
            self.query_one("#root-desc", Static).update(escape(desc))
        else:
            self.query_one("#root-desc", Static).update(
                "[dim]No description yet. Add context for this investigation.[/]"
            )
        ws = self.workspace
        queries = list(inv.queries.values()) if inv else []
        saved_qs = [q for q in queries if q.in_report]
        drafts = sum(1 for q in queries if not q.in_report)
        remark_n = sum(len(q.remarks or []) for q in saved_qs)
        self.query_one("#root-stats", Label).update(
            f"{len(saved_qs)} saved quer{'y' if len(saved_qs) == 1 else 'ies'}     "
            f"{drafts} draft{'s' if drafts != 1 else ''}     "
            f"{remark_n} remark{'s' if remark_n != 1 else ''}     "
            f"{len(ws.entity)} entities     "
            f"{len(ws.metric)} metrics"
        )
        report_lines: list[str] = []
        # Project-level summary when multiple investigation tabs exist.
        project = getattr(self.app, "project", None)
        order = list(getattr(project, "investigations", None) or [])
        titles = getattr(self.app, "_inv_titles", {}) or {}
        if len(order) > 1:
            report_lines.append(
                f"[bold]Project report[/] · {len(order)} investigations"
            )
            active_id = getattr(getattr(self.app, "active", None), "investigation", None)
            for inv_id in order:
                mark = "▸ " if inv_id == active_id else "  "
                label = titles.get(inv_id) or inv_id
                report_lines.append(f"{mark}{escape(str(label))}")
            report_lines.append("")
        report_lines.append("[bold]Saved queries[/]")
        if not saved_qs:
            report_lines.append("[dim]  None yet — open a draft and plot to save.[/]")
        else:
            for q in sorted(saved_qs, key=lambda x: x.id):
                name = escape(q.name or f"Query {q.id}")
                n_e = len(q.entity)
                n_m = len(q.metric)
                n_r = len(q.remarks or [])
                meta = f"{n_e} entit{'y' if n_e == 1 else 'ies'} · {n_m} metric{'s' if n_m != 1 else ''}"
                if n_r:
                    meta += f" · {n_r} remark{'s' if n_r != 1 else ''}"
                report_lines.append(f"  [bold]{name}[/]")
                report_lines.append(f"  [dim]{meta}[/]")
        collected = len(ws.entity) + len(ws.metric) + len(ws.series)
        report_lines.append("")
        report_lines.append(
            f"[bold]Workspace[/] · {collected} collected resource"
            f"{'s' if collected != 1 else ''}"
        )
        self.query_one("#root-report", Static).update("\n".join(report_lines))
        notes = (inv.annotations if inv else None) or ""
        self.query_one("#root-notes", Static).update(
            escape(notes)
            if notes
            else "[dim]  No research notes yet. Press a to add.[/]"
        )
        self.refresh_footer()

    def shell_breadcrumb(self) -> str:
        return "Overview"

    def footer_status(self) -> list[str]:
        return []

    def footer_action_keys(self) -> list[str]:
        return []

    def action_add_note(self) -> None:
        note = getattr(self.app, "edit_investigation_annotations", None)
        if callable(note):
            note()
        else:
            self.notify("Could not open notes editor", severity="warning")

    def action_load_releases(self) -> None:
        if self._releases_loading:
            return
        # Explicit ``r`` refreshes from the network; cache still updated.
        self.load_releases(refresh=True)

    def action_cursor_down(self) -> None:
        if not self._releases_loaded or self._editing:
            return
        self.query_one("#root-releases-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if not self._releases_loaded or self._editing:
            return
        self.query_one("#root-releases-list", ListView).action_cursor_up()

    def action_open_release(self) -> None:
        if not self._releases_loaded or self._editing:
            return
        row = self.query_one("#root-releases-list", ListView).highlighted_child
        if isinstance(row, _ReleaseRow):
            self._open_release(row.release)

    def _open_release(self, release: Release) -> None:
        open_fn = getattr(self.app, "open_resource_view", None)
        if not callable(open_fn):
            self.notify("Host cannot open releases", severity="error")
            return
        label = _release_display_name(release)
        if hasattr(self.app, "remember_label"):
            self.app.remember_label(release.id, label)
        open_fn("release", release.id, label=label)

    @on(ListView.Selected, "#root-releases-list")
    def _release_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _ReleaseRow):
            self._open_release(event.item.release)

    def _apply_releases(self, releases: list[Release], *, from_cache: bool) -> None:
        listing = self.query_one("#root-releases-list", ListView)
        prompt = self.query_one("#root-releases-prompt", Static)
        status = self.query_one("#root-releases-status", Static)
        self._releases = list(releases)
        self._releases_loaded = True
        listing.clear()
        listing.display = True
        prompt.display = False
        try:
            # Keep the load button so ``r`` / click can refresh.
            btn = self.query_one("#root-load-releases", ActionButton)
            btn.set_label("Refresh releases", shortcut="r")
        except Exception:  # noqa: BLE001
            pass
        remember = getattr(self.app, "remember_label", None)
        for rel in self._releases:
            if callable(remember):
                remember(rel.id, _release_display_name(rel))
            listing.append(_ReleaseRow(rel))
        if self._releases:
            listing.index = 0
            src = "cached" if from_cache else "live"
            status.update(
                f"{len(self._releases)} recent release"
                f"{'s' if len(self._releases) != 1 else ''} ({src}) · Enter opens"
            )
        else:
            status.update("No recent releases")
        self.refresh_footer()

    @work(exclusive=True, group="root-releases")
    async def load_releases(self, *, refresh: bool = False) -> None:
        self._releases_loading = True
        status = self.query_one("#root-releases-status", Static)
        cached = self._releases_cache_hit()
        if refresh:
            status.update("Refreshing recent releases…")
        elif cached:
            status.update("Loading cached releases…")
        else:
            status.update("Loading recent releases…")

        def _fetch() -> list[Release]:
            if refresh and hasattr(self.client, "uncached"):
                with self.client.uncached():
                    return self.client.list_releases(
                        RELEASES_PAGE, 0, "desc"
                    )
            return self.client.list_releases(RELEASES_PAGE, 0, "desc")

        try:
            raw = await asyncio.to_thread(_fetch)
        except Exception as exc:  # noqa: BLE001
            self._releases_loading = False
            status.update(f"Could not load releases: {exc}")
            return
        self._releases_loading = False
        self._apply_releases(list(raw or []), from_cache=cached and not refresh)
        # Only steal focus on an explicit load/refresh, not warm cache hydrate.
        if self._releases and refresh:
            try:
                self.query_one("#root-releases-list", ListView).focus()
            except Exception:  # noqa: BLE001
                pass

    @on(ActionButton.Pressed)
    def _button(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "publish":
            self.action_publish()
        elif event.button_id == "rename":
            self.action_rename()
        elif event.button_id == "edit_description":
            self.action_edit_description()
        elif event.button_id == "add_note":
            self.action_add_note()
        elif event.button_id == "load_releases":
            self.action_load_releases()


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
