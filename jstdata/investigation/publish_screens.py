"""TUI screens for `:publish`: confirm what will be shared, then show the link."""

from __future__ import annotations

import webbrowser

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, Static, TextArea

from .model import Investigation

# Longer query lists are summarised so the dialog fits a small terminal.
MAX_LISTED_QUERIES = 8


def describe_publication(inv: Investigation) -> str:
    """What `:publish` will share, as shown in the confirmation."""
    queries = sorted(inv.queries.values(), key=lambda q: q.id)
    lines = [
        f"[bold]{escape(inv.title)}[/bold]  ({escape(inv.id)})",
        "",
        f"{len(queries)} quer{'y' if len(queries) == 1 else 'ies'}:",
    ]
    for q in queries[:MAX_LISTED_QUERIES]:
        lines.append(f"  {q.id}. {escape(q.name or f'Query {q.id}')}")
    if len(queries) > MAX_LISTED_QUERIES:
        lines.append(f"  … and {len(queries) - MAX_LISTED_QUERIES} more")
    lines += [
        "",
        "Anyone with the link will see these queries, their annotations "
        "and results. The queries run with your API key.",
    ]
    return "\n".join(lines)


class PublishConfirmModal(ModalScreen[str | None]):
    """Ask before publishing, listing exactly what becomes public.

    Dismisses with ``"new"`` (publish a new link), ``"update"`` (replace the
    text at ``previous_url``, offered when the investigation was published
    before) or None (cancel).
    """

    DEFAULT_CSS = """
    PublishConfirmModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #publish-box {
        width: 72;
        height: auto;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #publish-kicker { color: #4ade80; text-style: bold; margin-bottom: 1; }
    #publish-body { color: #e0e0e0; height: auto; margin-bottom: 1; }
    #publish-hint { color: #888; }
    """

    BINDINGS = [
        Binding("y", "yes", "Publish", show=False),
        Binding("u", "update", "Update", show=False),
        Binding("p", "new", "New link", show=False),
        Binding("n", "no", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, inv: Investigation, previous_url: str | None = None) -> None:
        super().__init__()
        self._inv = inv
        self._previous = previous_url

    def compose(self) -> ComposeResult:
        with Vertical(id="publish-box"):
            yield Label("PUBLISH", id="publish-kicker")
            body = describe_publication(self._inv)
            if self._previous is not None:
                body += (
                    f"\n\nAlready published at [bold]{escape(self._previous)}[/bold]. "
                    "Update that page's text, or publish a new link? Updating keeps "
                    "its results; if you changed the queries, publish a new link."
                )
            yield Static(body, id="publish-body", markup=True)
            hint = (
                "[bold]u[/bold] update that page  ·  [bold]p[/bold] new link  ·  "
                "[bold]esc[/bold] cancel"
                if self._previous is not None
                else "[bold]y[/bold] publish  ·  [bold]n[/bold]/[bold]esc[/bold] cancel"
            )
            yield Label(hint, id="publish-hint", markup=True)

    def action_yes(self) -> None:
        if self._previous is None:
            self.dismiss("new")

    def action_update(self) -> None:
        if self._previous is not None:
            self.dismiss("update")

    def action_new(self) -> None:
        if self._previous is not None:
            self.dismiss("new")

    def action_no(self) -> None:
        # With a previous link, "n" is too close to "new link": only esc cancels.
        if self._previous is None:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PublishedModal(ModalScreen[None]):
    """Success screen after publish: link, copy, and open."""

    DEFAULT_CSS = """
    PublishedModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.45);
    }
    #published-box {
        width: 76;
        height: auto;
        border: tall #4ade80;
        background: #0e0e0e;
        padding: 2 3;
    }
    #published-box.copied {
        border: tall #bbf7d0;
        background: #052e16;
    }
    #published-kicker {
        color: #86efac;
        text-style: bold;
        margin-bottom: 0;
    }
    #published-headline {
        color: #f5f5f5;
        text-style: bold;
        margin-bottom: 1;
    }
    #published-url {
        color: #4ade80;
        text-style: bold;
        margin-bottom: 0;
        padding: 1 1;
        background: #101a14;
    }
    #published-url.copied {
        color: #052e16;
        background: #86efac;
        text-style: bold;
    }
    #published-copy-status {
        height: 1;
        margin-bottom: 1;
        color: #737373;
    }
    #published-copy-status.copied {
        color: #bbf7d0;
        text-style: bold;
        background: #14532d;
    }
    #published-body {
        color: #a3a3a3;
        height: auto;
        margin-bottom: 1;
    }
    #published-hint { color: #737373; }
    #published-hint.copied {
        color: #86efac;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("c", "copy", "Copy link", show=False),
        Binding("o", "open", "Open in browser", show=False),
        Binding("escape", "close", "Close", show=False),
        Binding("enter", "close", "Close", show=False),
    ]

    _HINT = (
        "[bold]c[/bold] copy link  ·  [bold]o[/bold] open in browser  ·  "
        "[bold]esc[/bold] / [bold]enter[/bold] close"
    )
    _HINT_COPIED = (
        "[bold]✓ COPIED TO CLIPBOARD[/bold]  ·  [bold]o[/bold] open  ·  "
        "[bold]esc[/bold] / [bold]enter[/bold] close"
    )

    def __init__(
        self,
        url: str,
        *,
        updated: bool = False,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.updated = updated
        self.report_title = (title or "").strip() or None
        self._copied = False

    def compose(self) -> ComposeResult:
        with Vertical(id="published-box"):
            yield Label(
                "✓ UPDATED" if self.updated else "✓ PUBLISHED",
                id="published-kicker",
            )
            headline = (
                "Your page is live on the same link."
                if self.updated
                else "Your research page is ready to share."
            )
            if self.report_title:
                headline = f"{escape(self.report_title)}\n{headline}"
            yield Static(headline, id="published-headline", markup=True)
            yield Label(self.url, id="published-url", markup=False)
            yield Label("Press [bold]c[/bold] to copy this link", id="published-copy-status", markup=True)
            body = (
                "The page's text is updated on the same link; its results are "
                "unchanged."
                if self.updated
                else "Anyone with this link can read the investigation — charts, "
                "remarks, and the evidence trail. Publishing again makes a new "
                "link; this one keeps results as of now."
            )
            yield Static(body, id="published-body", markup=True)
            yield Label(self._HINT, id="published-hint", markup=True)

    def action_copy(self) -> None:
        # Terminal clipboard (OSC 52); unsupported terminals ignore it, and
        # the link stays on screen to select by hand.
        self.app.copy_to_clipboard(self.url)
        self._copied = True
        box = self.query_one("#published-box")
        box.add_class("copied")
        url = self.query_one("#published-url", Label)
        url.add_class("copied")
        status = self.query_one("#published-copy-status", Label)
        status.update("✓  COPIED TO CLIPBOARD")
        status.add_class("copied")
        hint = self.query_one("#published-hint", Label)
        hint.update(self._HINT_COPIED)
        hint.add_class("copied")

    def action_open(self) -> None:
        webbrowser.open(self.url)

    def action_close(self) -> None:
        self.dismiss(None)



class PublishDraftScreen(Screen[str | None]):
    """The whole publication as one Markdown draft to fill in before publishing.

    Dismisses with the edited text (ctrl+s) or None (esc). ``error`` is shown
    above the editor when a previous attempt couldn't be applied, so the
    author fixes it without losing their edits.
    """

    DEFAULT_CSS = """
    PublishDraftScreen {
        background: #0a0a0a;
        padding: 1 2;
    }
    #draft-kicker { color: #4ade80; text-style: bold; height: 1; }
    #draft-help { color: #888; height: auto; margin-bottom: 1; }
    #draft-error { color: #f87171; height: auto; margin-bottom: 1; }
    #draft-body {
        height: 1fr;
        background: #111111;
        border: solid #333;
    }
    #draft-hint { color: #888; height: 1; margin-top: 1; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Continue", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, draft: str, *, title: str, error: str | None = None) -> None:
        super().__init__()
        self._draft = draft
        self._title = title
        self._error = error

    def compose(self) -> ComposeResult:
        yield Label(f"PUBLISH · {escape(self._title)}", id="draft-kicker")
        yield Static(
            "Write the page's text here: a title, a one-line description, an "
            "overview, and a note for each query's charts. Markdown works. "
            "Lines in <!-- --> are guidance and are left out.",
            id="draft-help",
            markup=False,
        )
        if self._error is not None:
            yield Static(self._error, id="draft-error", markup=False)
        yield TextArea(self._draft, id="draft-body", soft_wrap=True)
        yield Label(
            "[bold]ctrl+s[/bold] save and continue  ·  [bold]esc[/bold] cancel",
            id="draft-hint",
            markup=True,
        )

    def on_mount(self) -> None:
        self.query_one("#draft-body", TextArea).focus()

    def action_save(self) -> None:
        self.dismiss(self.query_one("#draft-body", TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)


def edit_publish_draft(inv: Investigation, web_versions=None):
    """Run the draft editor on its own (for `jst publish`).

    Returns ``(updated_investigation, changes)``, or None if the author
    cancelled. A draft that can't be applied reopens with the error and the
    author's text, so nothing typed is lost.
    """
    from textual.app import App

    from ..publish_draft import DraftError, apply_draft, build_draft

    class _DraftApp(App[str | None]):
        def __init__(self, text: str, error: str | None) -> None:
            super().__init__()
            self._text = text
            self._error = error

        def on_mount(self) -> None:
            self.push_screen(
                PublishDraftScreen(self._text, title=inv.title, error=self._error),
                self.exit,
            )

    text, error = build_draft(inv, web_versions), None
    while True:
        result = _DraftApp(text, error).run()
        if result is None:
            return None
        try:
            return apply_draft(inv, result)
        except DraftError as exc:
            text, error = result, str(exc)
