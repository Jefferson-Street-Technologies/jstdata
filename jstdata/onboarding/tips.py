"""Contextual tips: a short tip per screen saying what you can do there.

Shown outside tutorials (the coach bar takes over during one). Keyed off the
same :class:`CoachSnapshot` the coach uses, so screens need no hooks. Write
each tip as instructions: name the key in quotes and say what it does
(``Press "2" to open Search``). Avoid ``[...]``; Textual parses it as markup.
"""

from __future__ import annotations

from .tutorials import CoachSnapshot

_TYPING = (
    'You\'re typing in a text box. Press "Enter" to jump to the results, or '
    '"Esc" to leave the box so the other keys work again.'
)

_HOME = {
    None: (
        'Press "/" to search, "Q"/"w" to jump Queries or Workspace, '
        'or "n" for a new draft query. "t" opens tutorials.'
    ),
    "recent": (
        'These are the newest filings and data releases. Press "j"/"k" to move '
        'and "Enter" to open one.'
    ),
    "search": (
        'Press "Space" to collect the highlighted result, or "Enter" to open it. '
        'Press "/" to search again.'
    ),
    "actions": (
        'Views in this investigation. Press "Enter" to reopen one.'
    ),
    "workspace": (
        'Collected resources. Press "Enter" to open one, or "n" / workspace '
        'query to chart them.'
    ),
    "queries": (
        'One draft lives in the sidebar. Press "s" to save it into the report.'
    ),
}

_SCREENS = {
    "ResearchRootScreen": (
        'Click a sidebar query to open it, or press "Q" then a digit to jump. '
        '"w" jumps workspace resources. "/" searches; "n" creates a query.'
    ),
    "MetricScreen": (
        'Tab switches Entities / Definition. "Enter" on an entity opens a draft '
        'chart. "f" filters; "a" adds this metric to the workspace.'
    ),
    "EntityScreen": (
        'Tab switches Metrics / Relationships. "Enter" on a metric opens a draft '
        'chart. "f" filters metrics; "a" adds this entity to the workspace.'
    ),
    "MetricsScreen": (
        'Press "r" to load metrics. "Space" collects; "Enter" opens one.'
    ),
    "QueryScreen": (
        'Compose in the header: "E"/"M"/"S" add; "r" refreshes. Tabs above the chart '
        'switch Chart/Table ("c"/"t"). Table Enter opens a release. Annotate at bottom.'
    ),
    "SeriesScreen": (
        '"Enter" or "p" plots this series. "m"/"e" open the parent metric or entity.'
    ),
    "TableScreen": (
        'Press "r" to load values. "Enter" on a cell opens the entity or metric.'
    ),
    "ReleaseScreen": (
        'Press "r" to load this release\'s details.'
    ),
    "WorkspaceScreen": (
        'Press "/" to search and collect into the workspace.'
    ),
    "DiscoverScreen": (
        '"Enter" previews numbers; "Space" collects; "/" filters.'
    ),
    "RankScreen": (
        'Highlight a metric to rank by it, then "Enter" on a row to collect.'
    ),
    "WorkspaceDrawer": (
        'Workspace drawer. "j"/"k" move; "Esc" closes.'
    ),
    "SearchDrawer": (
        'Type to search. "Space" collects; "Esc" closes.'
    ),
    "CommandPalette": (
        'Type a command and press "Enter". "tips on" restores contextual tips. '
        '"Esc" closes.'
    ),
}

_FALLBACK = (
    'Press "/" to search, "Q"/"w" to jump the sidebar, "Ctrl+o" to go back, '
    '":" for commands, or "?" for keys.'
)


# Where "next step" nudges replace the screen tip: places you collect from.
_NUDGE_SCREENS = frozenset({"HomeScreen", "SearchDrawer", "WorkspaceDrawer", "WorkspaceScreen"})


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def next_step_nudge(snap: CoachSnapshot) -> str | None:
    """Suggest the next thing to collect, based on what's in the workspace."""
    n_ent, n_met = len(snap.entities), len(snap.metrics)
    in_search = snap.screen == "HomeScreen" and snap.home_dest == "search"
    if in_search:
        # "/" keeps the previous query in the box; say how to clear it.
        search_how = 'press "/" then "Ctrl+U" to clear the box'
    elif snap.screen == "HomeScreen":
        search_how = 'press "2" to open Search'
    else:
        search_how = 'press "/"'

    if n_ent >= 2 and n_met == 0:
        return (
            f"You've collected {_count(n_ent, 'entity', 'entities')}. "
            f"Next, add a metric to compare them on: {search_how}, type a measure "
            'such as revenue or GDP, then "Space" on a METRIC row ("f" shows only metrics).'
        )
    if n_met >= 1 and n_ent == 0:
        return (
            f"You've collected {_count(n_met, 'metric', 'metrics')}. Next, add the "
            f"companies or countries to compare: {search_how}, type a name, then "
            '"Space" to add it. Or open the metric and press "r" to list who reports it.'
        )
    if n_met >= 1 and n_ent >= 1 and snap.query_count == 0 and snap.screen == "HomeScreen":
        have = (
            f"You have {_count(n_met, 'metric', 'metrics')} and "
            f"{_count(n_ent, 'entity', 'entities')}, enough to compare."
        )
        # Home only binds "c" (query from workspace) on Workspace and Queries.
        if snap.home_dest in ("workspace", "queries"):
            return f'{have} Press "c" to turn them into a query and see the numbers.'
        if snap.home_dest == "search":
            return (
                f'{have} Keep adding with "Space", or press "2" for Workspace '
                'and build a query with "n".'
            )
        return (
            f'{have} Press "2" to review the workspace, or "n" for a new draft '
            "query."
        )
    return None


def tip_for(snap: CoachSnapshot) -> str:
    """The tip for what's on screen right now."""
    if snap.focus_in_input and snap.screen != "CommandPalette":
        return _TYPING
    if snap.screen in _NUDGE_SCREENS:
        nudge = next_step_nudge(snap)
        if nudge:
            return nudge
    if snap.screen == "HomeScreen":
        return _HOME.get(snap.home_dest, _HOME[None])
    return _SCREENS.get(snap.screen, _FALLBACK)
