"""Right drawer: research activity trail for the open investigation (Shift+L)."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from ..ops.event_present import (
    PresentedEvent,
    actor_filter_label,
    count_trail_stats,
    format_event,
    is_retrieve_op,
)
from ..ops.runner import get_store

# Cycle: all → tui → cli → agent → all
_ACTOR_FILTERS: tuple[Optional[str], ...] = (None, "tui", "cli", "agent")

_KIND_BULLET = {
    "retrieve": "#4ade80",
    "search": "#93c5fd",
    "explore": "#93c5fd",
    "remark": "#e5e5e5",
    "mutate": "#e5e5e5",
    "error": "#fbbf24",
    "other": "#a3a3a3",
}

# Actor chips — distinct from dim timestamps so attribution catches the eye.
_ACTOR_COLOR = {
    "You": "#86efac",
    "CLI": "#fcd34d",
    "Agent": "#c4b5fd",
}


def _actor_markup(label: str) -> str:
    """Bold colored actor token (Agent / You / CLI…)."""
    safe = escape(label)
    if label.startswith("Agent"):
        color = _ACTOR_COLOR["Agent"]
    else:
        color = _ACTOR_COLOR.get(label, "#fda4af")
    return f"[bold {color}]{safe}[/]"


class _DayHeader(ListItem):
    def __init__(self, label: str) -> None:
        super().__init__(Label(f"[bold #737373]{label}[/]", markup=True))
        self.can_focus = False


class _EventCard(ListItem):
    """Timeline card for one presented event."""

    def __init__(self, presented: PresentedEvent, *, expanded: bool = False) -> None:
        self.presented = presented
        self.expanded = expanded
        self.event_id = presented.event_id
        super().__init__(Static(self._render_markup(), markup=True))

    def _render_markup(self) -> str:
        p = self.presented
        bullet = _KIND_BULLET.get(p.kind, "#a3a3a3")
        title = escape(p.title)
        when = escape(p.when)
        lines = [
            f"[bold {bullet}]●[/] [bold #f5f5f5]{title}[/]"
            f"  [dim]{when}[/]  {_actor_markup(p.actor_label)}"
        ]
        for sec in p.secondary_lines:
            lines.append(f"[#a3a3a3]│ {escape(sec)}[/]")
        if p.stats_line and not self.expanded:
            lines.append(f"[#a3a3a3]│ {escape(p.stats_line)}[/]")
        if self.expanded:
            if p.stats_line:
                lines.append(f"[#86efac]│ {escape(p.stats_line)}[/]")
            if p.preview_rows:
                lines.append("[#52525b]│ ┌──────────────────────────────┐[/]")
                for row in p.preview_rows:
                    cell = f"{row.label[:22]:<22} {row.period:<9} {row.value:>8}"
                    lines.append(f"[#e5e5e5]│ │ {escape(cell)}[/]")
                lines.append("[#52525b]│ └──────────────────────────────┘[/]")
            elif p.kind == "retrieve":
                lines.append("[dim]│ (no sample observations retained)[/]")
            hint = "Space collapse · Enter details · r rerun"
            lines.append(f"[dim]│ {hint}[/]")
        elif p.expandable or is_retrieve_op(p.operation):
            lines.append("[dim]│ Space expand · Enter details[/]")
        return "\n".join(lines)


class ActivityDrawer(ModalScreen[Optional[str]]):
    """Research activity trail; Enter inspects, Space expands, r reruns."""

    BINDINGS = [
        Binding("escape", "dismiss_drawer", "Close", show=True),
        Binding("enter", "open_event", "Inspect", show=True),
        Binding("space", "toggle_expand", "Expand", show=True),
        Binding("r", "rerun_event", "Rerun", show=True),
        Binding("a", "cycle_actor", "Filter actor", show=True),
        Binding("i", "open_event", "Inspect", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    CSS = """
    ActivityDrawer {
        align: right middle;
        background: transparent;
    }
    #activity-panel {
        width: 68;
        height: 100%;
        border-left: solid #2a2a2a;
        background: #0e0e0e;
        padding: 1 1;
    }
    #activity-title {
        text-style: bold;
        color: #f5f5f5;
        height: 1;
        margin-bottom: 0;
    }
    #activity-subtitle {
        color: #737373;
        height: 1;
        margin-bottom: 0;
    }
    #activity-filter {
        color: #6a6a6a;
        height: 1;
        margin-bottom: 1;
    }
    #activity-list {
        height: 1fr;
        background: transparent;
        border: none;
    }
    #activity-list > ListItem {
        background: transparent;
        padding: 0 0 1 0;
        height: auto;
        min-height: 3;
    }
    #activity-list > ListItem.--highlight {
        background: #122016;
    }
    #activity-list > ListItem.-expanded {
        background: #101a14;
    }
    """

    def __init__(
        self,
        *,
        project_id: Optional[str],
        investigation_id: Optional[str],
        app_dir=None,
        limit: int = 40,
        actor_filter: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.project_id = project_id
        self.investigation_id = investigation_id
        self.app_dir = app_dir
        self.limit = limit
        self._actor_filter = actor_filter
        self._events: list[dict[str, Any]] = []
        self._presented: dict[str, PresentedEvent] = {}
        self._expanded_id: Optional[str] = None
        self._artifact_cache: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="activity-panel"):
            yield Static("Research activity", id="activity-title")
            yield Static("", id="activity-subtitle")
            yield Static(self._filter_label(), id="activity-filter")
            yield ListView(id="activity-list")
            yield Footer()

    def on_mount(self) -> None:
        self._reload()

    def _filter_label(self) -> str:
        label = actor_filter_label(self._actor_filter)
        return f"Filter: {label}  (a cycles All · You · CLI · Agent)"

    def _subtitle(self) -> str:
        n, m = count_trail_stats(self._events)
        if n == 0:
            return "No actions yet"
        return f"{n} action{'s' if n != 1 else ''} · {m} data request{'s' if m != 1 else ''}"

    def _reload(self) -> None:
        store = get_store(app_dir=self.app_dir)
        self._events = store.list_events(
            project_id=self.project_id,
            investigation_id=self.investigation_id,
            actor=self._actor_filter,
            limit=self.limit,
        )
        self._presented.clear()
        self._expanded_id = None
        self._artifact_cache.clear()
        try:
            self.query_one("#activity-filter", Static).update(self._filter_label())
            self.query_one("#activity-subtitle", Static).update(self._subtitle())
        except Exception:  # noqa: BLE001
            pass
        self._rebuild_list()

    def _present(self, event: dict[str, Any]) -> PresentedEvent:
        eid = str(event.get("event_id") or "")
        if eid in self._presented:
            return self._presented[eid]
        artifact_data = None
        art_id = event.get("artifact_id")
        if eid == self._expanded_id and art_id:
            artifact_data = self._load_artifact(str(art_id))
        presented = format_event(event, artifact_data=artifact_data)
        self._presented[eid] = presented
        return presented

    def _load_artifact(self, artifact_id: str) -> Any:
        if artifact_id in self._artifact_cache:
            return self._artifact_cache[artifact_id]
        store = get_store(app_dir=self.app_dir)
        try:
            data = store.load_artifact_data(artifact_id)
        except Exception:  # noqa: BLE001
            data = None
        self._artifact_cache[artifact_id] = data
        return data

    def _rebuild_list(self) -> None:
        listing = self.query_one("#activity-list", ListView)
        listing.clear()
        if not self._events:
            listing.append(ListItem(Label("(no events yet)")))
            return
        today = date.today().isoformat()
        last_day: Optional[str] = None
        focus_index = 0
        idx = 0
        for event in self._events:
            presented = self._present(event)
            day = presented.day_key
            if day and day != last_day:
                if day == today:
                    header = "TODAY"
                else:
                    header = day
                listing.append(_DayHeader(header))
                idx += 1
                last_day = day
            expanded = presented.event_id == self._expanded_id
            card = _EventCard(presented, expanded=expanded)
            if expanded:
                card.add_class("-expanded")
            listing.append(card)
            if expanded:
                focus_index = idx
            idx += 1
        listing.index = min(focus_index, max(0, len(listing.children) - 1))

    def _selected_card(self) -> Optional[_EventCard]:
        listing = self.query_one("#activity-list", ListView)
        item = listing.highlighted_child
        return item if isinstance(item, _EventCard) else None

    def action_cycle_actor(self) -> None:
        try:
            idx = _ACTOR_FILTERS.index(self._actor_filter)
        except ValueError:
            idx = 0
        self._actor_filter = _ACTOR_FILTERS[(idx + 1) % len(_ACTOR_FILTERS)]
        self._reload()

    def action_dismiss_drawer(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#activity-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#activity-list", ListView).action_cursor_up()

    def action_toggle_expand(self) -> None:
        card = self._selected_card()
        if card is None:
            return
        eid = card.event_id
        if self._expanded_id == eid:
            self._expanded_id = None
        else:
            self._expanded_id = eid
        # Drop cached presentation so artifact preview can fill in.
        self._presented.pop(eid, None)
        self._rebuild_list()

    def action_open_event(self) -> None:
        card = self._selected_card()
        if card is not None:
            self.dismiss(
                {"event_id": card.event_id, "mode": "inspect"}
            )
            return
        self.dismiss(None)

    def action_rerun_event(self) -> None:
        card = self._selected_card()
        if card is not None:
            self.dismiss(
                {"event_id": card.event_id, "mode": "rerun"}
            )
            return
        self.dismiss(None)

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _EventCard):
            # Enter-equivalent from mouse: inspect.
            self.dismiss(
                {"event_id": item.event_id, "mode": "inspect"}
            )
