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
        'New here? Press "2" to open Search and look up a company, country, or '
        'metric. Press "1"–"5" (or "j"/"k" then "Enter") to open the other '
        'destinations, and "t" for a guided tutorial.'
    ),
    "recent": (
        'These are the newest filings and data releases. Press "j"/"k" to move '
        'and "Enter" to open one. Press "Esc" to go back to the menu.'
    ),
    "search": (
        'Press "Space" to add the highlighted result to your workspace, or '
        '"Enter" to open it in its own view. Press "/" to search for something '
        'else, or "f" to show only metrics, entities, or series.'
    ),
    "actions": (
        'Every view you\'ve opened in this investigation. Press "Enter" to jump '
        'back into one, or "Esc" to go back to the menu.'
    ),
    "workspace": (
        'Everything you\'ve collected. Press "j"/"k" to move, "Backspace" to '
        'remove the highlighted item, or "Enter" to open it. Press "c" to turn '
        'it all into a query (every metric × every entity) and see the numbers.'
    ),
    "queries": (
        'Press "Enter" on "Create from workspace" (or "c") to save a query of '
        'every metric × every entity you\'ve collected. Press "Enter" on a saved '
        'query to open and run it.'
    ),
}

_SCREENS = {
    "MetricScreen": (
        'Press "r" to load what this metric means and who reports it. Then press '
        '"/" to filter that list, "Space" to add an entry to your workspace, or '
        '"Enter" to open it. Press "a" to add this metric itself.'
    ),
    "EntityScreen": (
        'Press "r" to load this entity\'s metrics and relationships. "Tab" '
        'switches between the two lists, "Space" adds the highlighted item, and '
        '"Enter" opens a metric. Press "a" to add this entity itself.'
    ),
    "MetricsScreen": (
        'Press "r" to load the metrics your entities report. "m" switches between '
        'metrics any of them report and ones all of them report. "Space" adds '
        'one; "Enter" opens it.'
    ),
    "QueryScreen": (
        'Press "r" to run this query. Then press "c" for a chart or "t" for a '
        'table. "[" and "]" flip between charts, "y" copies Python, "e" exports '
        'a CSV.'
    ),
    "TableScreen": (
        'Press "r" to load the latest values. "Tab" switches between rows and '
        'columns, and "Enter" on a cell opens the entity, metric, or release '
        'behind it.'
    ),
    "ReleaseScreen": (
        'Press "r" to load this release\'s details: filing label, date, and source.'
    ),
    "WorkspaceScreen": (
        'Press "/" and type to search. "Enter" jumps to the results; "Enter" '
        'again adds the highlighted item to your workspace.'
    ),
    "DiscoverScreen": (
        'These are metrics your entities report. Press "Enter" to preview the '
        'latest numbers, "Space" to add one to your workspace, or "/" to filter.'
    ),
    "RankScreen": (
        'Highlight a metric on the left to rank by it. Press "Enter" to move into '
        'the ranking, then "Enter" on a row to add that entity to your workspace.'
    ),
    "WorkspaceDrawer": (
        'Your workspace. Press "j"/"k" to move, "Backspace" to remove an item, '
        'and "Esc" to close.'
    ),
    "SearchDrawer": (
        'Type to search. Press "Space" to add the highlighted result to your '
        'workspace, or "Esc" to close.'
    ),
    "CommandPalette": (
        'Type a command and press "Enter": workspace, search, query, rename, '
        'add, rm, tutorial, tips. "Tab" completes; "Esc" closes.'
    ),
}

_FALLBACK = (
    'Press "?" to see this screen\'s keys, "g" to go Home, or ":" to type a '
    'command.'
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
                f'{have} Keep adding with "Space", or press "Esc" then "4" to '
                'review your workspace and "c" to turn it into a query.'
            )
        return (
            f'{have} Press "4" to review your workspace, then "c" to turn it '
            "into a query and see the numbers."
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
