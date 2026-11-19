"""TUI screens for `:publish`: confirm what will be shared, then show the link."""

from __future__ import annotations

import webbrowser

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

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


class PublishConfirmModal(ModalScreen[bool]):
    """Ask before publishing, listing exactly what becomes public."""

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
        Binding("n", "no", "Cancel", show=False),
        Binding("escape", "no", "Cancel", show=False),
    ]

    def __init__(self, inv: Investigation) -> None:
        super().__init__()
        self._inv = inv

    def compose(self) -> ComposeResult:
        with Vertical(id="publish-box"):
            yield Label("PUBLISH", id="publish-kicker")
            yield Static(describe_publication(self._inv), id="publish-body", markup=True)
            yield Label(
                "[bold]y[/bold] publish  ·  [bold]n[/bold]/[bold]esc[/bold] cancel",
                id="publish-hint",
                markup=True,
            )

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class PublishedModal(ModalScreen[None]):
    """The new publication's link, with copy and open shortcuts."""

    DEFAULT_CSS = """
    PublishedModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #published-box {
        width: 72;
        height: auto;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #published-kicker { color: #4ade80; text-style: bold; margin-bottom: 1; }
    #published-url { color: #ffffff; text-style: bold; margin-bottom: 1; }
    #published-body { color: #a3a3a3; height: auto; margin-bottom: 1; }
    #published-hint { color: #888; }
    """

    BINDINGS = [
        Binding("c", "copy", "Copy link", show=False),
        Binding("o", "open", "Open in browser", show=False),
        Binding("escape", "close", "Close", show=False),
        Binding("enter", "close", "Close", show=False),
    ]

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def compose(self) -> ComposeResult:
        with Vertical(id="published-box"):
            yield Label("PUBLISHED", id="published-kicker")
            yield Label(self.url, id="published-url", markup=False)
            yield Static(
                "Share this link. Publishing again makes a new link; this one "
                "keeps the results as of now.",
                id="published-body",
            )
            yield Label(
                "[bold]c[/bold] copy link  ·  [bold]o[/bold] open in browser  ·  "
                "[bold]esc[/bold] close",
                id="published-hint",
                markup=True,
            )

    def action_copy(self) -> None:
        # Terminal clipboard (OSC 52); unsupported terminals ignore it, and
        # the link stays on screen to select by hand.
        self.app.copy_to_clipboard(self.url)
        self.notify("Link copied")

    def action_open(self) -> None:
        webbrowser.open(self.url)

    def action_close(self) -> None:
        self.dismiss(None)
