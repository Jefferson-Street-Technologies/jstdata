"""Home destinations, action-tree projection, and palette discovery.

Pure helpers — no Textual. The host and screens consume these; the YAML
action tree and command grammar stay the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .model import ROOT_ACTION_ID, Investigation


DEST_RECENT = "recent"
DEST_SEARCH = "search"
DEST_ACTIONS = "actions"
DEST_WORKSPACE = "workspace"
DEST_QUERIES = "queries"

DEST_IDS: tuple[str, ...] = (
    DEST_RECENT,
    DEST_SEARCH,
    DEST_ACTIONS,
    DEST_WORKSPACE,
    DEST_QUERIES,
)

GOTO_PREFIX = "goto "


@dataclass(frozen=True)
class Destination:
    """One of Home's five permanent destinations."""

    id: str
    number: str
    title: str
    blurb: str
    inspector: str
    filters: str


DESTINATIONS: tuple[Destination, ...] = (
    Destination(
        id=DEST_RECENT,
        number="01",
        title="Recent",
        blurb="Latest filings and economic updates",
        inspector=(
            "A chronological feed of ingested SEC filings and economic "
            "updates. Filter by event type when a feed is available. "
            "Enter opens the matching resource view."
        ),
        filters="All · Relevant · Filings · Economic",
    ),
    Destination(
        id=DEST_SEARCH,
        number="02",
        title="Search",
        blurb="Find financial and economic data",
        inspector=(
            "Search the catalog for metrics, entities, and series. "
            "Type to filter; f cycles resource type. Enter opens a view; "
            "space collects into the workspace."
        ),
        filters="Resource type · Source · Frequency",
    ),
    Destination(
        id=DEST_ACTIONS,
        number="03",
        title="Actions",
        blurb="Explore investigation relationships",
        inspector=(
            "The investigation action tree — every view opened in this "
            "session. Nodes start large and shrink as the tree fills, "
            "as a record of how far exploration has gone. Enter resumes "
            "a view."
        ),
        filters="Tree outline",
    ),
    Destination(
        id=DEST_WORKSPACE,
        number="04",
        title="Workspace",
        blurb="Current working set",
        inspector=(
            "The live bag of staged metrics, entities, and series. "
            "Enter opens a resource view. Backspace removes. c snapshots "
            "the bag into a saved query."
        ),
        filters="Grouped by type",
    ),
    Destination(
        id=DEST_QUERIES,
        number="05",
        title="Queries",
        blurb="Saved observation queries",
        inspector=(
            "Reusable cartesian metric × entity specifications. "
            "Create from the workspace, open to view JSON results, or "
            "backspace to delete. Editing comes later."
        ),
        filters="Create · View · Delete",
    ),
)

_DEST_BY_ID = {d.id: d for d in DESTINATIONS}


def get_destination(dest_id: str) -> Destination:
    try:
        return _DEST_BY_ID[dest_id]
    except KeyError as exc:
        raise KeyError(f"Unknown destination {dest_id!r}") from exc


def workspace_counts_line(inv: Investigation) -> str:
    """Compact bag + query counts for the Home header (3M · 4E · 1Q)."""
    ws = inv.workspace
    return (
        f"{len(ws.metric)}M · {len(ws.entity)}E · {len(ws.series)}S · "
        f"{len(inv.queries)}Q"
    )


def inspector_copy(dest: Destination, inv: Investigation) -> str:
    """Inspector body for a highlighted destination, including live counts."""
    ws = inv.workspace
    extra = {
        DEST_RECENT: (
            "No unified ingestion feed is exposed yet. This destination "
            "is a placeholder until filings and economic updates can be "
            "listed together."
        ),
        DEST_SEARCH: (
            f"Workspace {len(ws.metric)} metrics · {len(ws.entity)} entities · "
            f"{len(ws.series)} series"
        ),
        DEST_ACTIONS: f"{len(inv.actions)} nodes in the action tree",
        DEST_WORKSPACE: (
            f"{len(ws.metric)} metrics · {len(ws.entity)} entities · "
            f"{len(ws.series)} series"
        ),
        DEST_QUERIES: f"{len(inv.queries)} saved quer"
        + ("y" if len(inv.queries) == 1 else "ies"),
    }
    return (
        f"{dest.inspector}\n\n"
        f"{extra[dest.id]}\n\n"
        f"AVAILABLE\n{dest.filters}"
    )


# ---------------------------------------------------------------------------
# Action tree (parent_id outline)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionTreeRow:
    """One outline row in the Actions destination."""

    action_id: int
    type: str
    description: str
    depth: int
    prefix: str
    is_last: bool
    child_count: int

    def title(self) -> str:
        label = self.description or self.type
        if self.action_id == ROOT_ACTION_ID:
            return f"{self.prefix}{label}"
        return f"{self.prefix}{label}  ·  {self.type} {self.action_id}"


def action_tree_rows(inv: Investigation) -> list[ActionTreeRow]:
    """DFS outline of the action tree, root first.

    The investigation is a parent_id tree; this is a projection, not a new
    graph. Box-drawing prefixes make parent/child visible without implying
    extra edges.
    """
    rows: list[ActionTreeRow] = []

    def walk(action_id: int, prefix: str, is_last: bool, depth: int) -> None:
        action = inv.get_action(action_id)
        kids = inv.children(action_id)
        if depth == 0:
            line_prefix = ""
            child_prefix = ""
        else:
            line_prefix = prefix + ("└─ " if is_last else "├─ ")
            child_prefix = prefix + ("   " if is_last else "│  ")
        desc = (action.description or "").strip() or (
            "Home" if action.type == "home" else action.type
        )
        rows.append(
            ActionTreeRow(
                action_id=action.id,
                type=action.type,
                description=desc,
                depth=depth,
                prefix=line_prefix,
                is_last=is_last,
                child_count=len(kids),
            )
        )
        for i, child in enumerate(kids):
            walk(child.id, child_prefix, i == len(kids) - 1, depth + 1)

    if ROOT_ACTION_ID in inv.actions:
        walk(ROOT_ACTION_ID, "", True, 0)
    return rows


def tree_row_height(node_count: int) -> int:
    """Cells per tree node: large when sparse, one row when dense.

    The size is a progress cue — a young investigation reads as a few
    generous blocks; a deep one compresses into an outline.
    """
    n = max(int(node_count), 1)
    if n <= 2:
        return 6
    if n <= 4:
        return 4
    if n <= 8:
        return 3
    if n <= 16:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Command palette discovery (adapter over existing : grammar)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaletteItem:
    """One searchable palette row. ``line`` is dismissed to the host."""

    label: str
    detail: str
    line: str
    kind: str  # dest, query, command, complete


def _fold_plural(word: str) -> str:
    """Cheap singular-ish form so ``query`` hits ``Queries``."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
        return word[:-1]
    return word


def fuzzy_score(query: str, *fields: str) -> Optional[int]:
    """Case-insensitive fuzzy score; ``None`` if ``query`` does not match.

    Empty query matches everything (score 0). A contiguous substring outranks
    a scattered subsequence. Higher is better.
    """
    needle = query.strip().lower()
    hay = " ".join(f for f in fields if f).lower()
    if not needle:
        return 0
    loc = hay.find(needle)
    if loc >= 0:
        return 1000 - loc
    folded_needle = _fold_plural(needle)
    for word in hay.replace("·", " ").replace("/", " ").replace(":", " ").split():
        folded = _fold_plural(word)
        if folded == folded_needle or word == needle:
            return 950
        if folded.startswith(folded_needle) or folded_needle.startswith(folded):
            if len(folded_needle) >= 3:
                return 850
    # Subsequence: every needle char appears in order.
    i = 0
    score = 0
    prev = -2
    for idx, ch in enumerate(hay):
        if i < len(needle) and ch == needle[i]:
            score += 3
            if idx == prev + 1:
                score += 4
            if idx == 0 or hay[idx - 1] in " ·:/_-":
                score += 2
            prev = idx
            i += 1
    if i < len(needle):
        return None
    return score


def list_palette_items(
    inv: Optional[Investigation],
    query: str = "",
) -> list[PaletteItem]:
    """Fuzzy-ranked destinations, saved queries, and : commands.

    Does not replace ``parse_command_line`` — items carry a command line the
    host already knows how to dispatch (plus ``goto <dest>``).
    """
    items: list[PaletteItem] = []
    for dest in DESTINATIONS:
        items.append(
            PaletteItem(
                label=f"Open {dest.title}",
                detail=dest.blurb,
                line=f"{GOTO_PREFIX}{dest.id}",
                kind="dest",
            )
        )
    items.append(
        PaletteItem(
            label="Go Home",
            detail="Return to the five destinations",
            line=f"{GOTO_PREFIX}home",
            kind="dest",
        )
    )
    if inv is not None:
        for qid in sorted(inv.queries):
            q = inv.queries[qid]
            name = q.name or f"query {qid}"
            items.append(
                PaletteItem(
                    label=f"Open Q{qid} · {name}",
                    detail=(
                        f"{len(q.metric)} metrics · {len(q.entity)} entities · "
                        f"{len(q.series)} series"
                    ),
                    line=f"query {qid}",
                    kind="query",
                )
            )
        items.append(
            PaletteItem(
                label="Create query from workspace",
                detail="Snapshot the bag as a saved query",
                line="query",
                kind="command",
            )
        )
    # Existing vim-grammar commands (labels, not a second dispatcher).
    from .commands import COMMANDS

    for spec in COMMANDS:
        items.append(
            PaletteItem(
                label=f":{spec.name}",
                detail=spec.summary,
                line=spec.name,
                kind="command",
            )
        )

    kind_bonus = {"dest": 40, "query": 20, "command": 0, "complete": 0}
    ranked: list[tuple[int, int, PaletteItem]] = []
    for idx, item in enumerate(items):
        score = fuzzy_score(query, item.label, item.detail, item.line)
        if score is None:
            continue
        ranked.append((score + kind_bonus.get(item.kind, 0), -idx, item))
    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [item for _, _, item in ranked]


def parse_goto(line: str) -> Optional[str]:
    """Return a destination id or ``home`` if ``line`` is a goto command."""
    text = line.strip()
    if text.startswith(":"):
        text = text[1:].strip()
    if not text.startswith(GOTO_PREFIX):
        return None
    dest = text[len(GOTO_PREFIX) :].strip().lower()
    if dest == "home" or dest in _DEST_BY_ID:
        return dest
    return None


# ---------------------------------------------------------------------------
# Action graph visual (unlabeled; the drawing is the point)
# ---------------------------------------------------------------------------

_NODE = "●"
_ROOT = "◉"
_SMALL = "•"
_STYLE_NODE = "bold #86efac"
_STYLE_ROOT = "bold #bbf7d0"
_STYLE_ACTIVE = "reverse bold #052e16 on #4ade80"
_STYLE_EDGE = "#4ade80"
_STYLE_SPARK = "#166534"


def render_action_graph(
    inv: Investigation,
    *,
    width: int = 56,
    height: int = 11,
    active_id: Optional[int] = None,
) -> str:
    """Bright layered drawing of the investigation action tree.

    Sparse trees sit large in the frame with a halo; dense trees pack into
    dots. No caption — the shape is the signal.
    """
    width = max(int(width), 16)
    height = max(int(height), 5)
    if ROOT_ACTION_ID not in inv.actions:
        return ""

    levels: list[list[int]] = []
    queue = [ROOT_ACTION_ID]
    while queue:
        levels.append(list(queue))
        nxt: list[int] = []
        for aid in queue:
            nxt.extend(child.id for child in inv.children(aid))
        queue = nxt

    n = sum(len(level) for level in levels)
    label_mode = n <= 6 and height >= len(levels) * 3
    stride = 3 if label_mode else 2
    if len(levels) * stride - 1 > height:
        stride = 2 if len(levels) * 2 - 1 <= height else 1
        label_mode = False

    max_depth = min(len(levels), (height + stride - 1) // stride)
    levels = levels[:max_depth]

    def xs_for(count: int) -> list[int]:
        inner = width - 2
        if count <= 0:
            return []
        if count == 1:
            return [width // 2]
        if count >= inner:
            return [1 + int(i * (inner - 1) / max(count - 1, 1)) for i in range(count)]
        gap = inner / (count + 1)
        return [int(gap * (i + 1)) for i in range(count)]

    pos: dict[int, tuple[int, int]] = {}
    for depth, level in enumerate(levels):
        ys = depth * stride
        for aid, x in zip(level, xs_for(len(level))):
            pos[aid] = (x, ys)

    chars = [[" " for _ in range(width)] for _ in range(height)]
    styles: list[list[Optional[str]]] = [[None] * width for _ in range(height)]

    def plot(x: int, y: int, ch: str, style: Optional[str]) -> None:
        if 0 <= x < width and 0 <= y < height:
            chars[y][x] = ch
            styles[y][x] = style

    # Halo for a young tree — keep it off the label/connector rows.
    if n <= 3:
        cx, cy = pos.get(ROOT_ACTION_ID, (width // 2, 0))
        for dx, dy in ((-6, 0), (6, 0), (-3, 0), (3, 0), (0, height - 1)):
            if (cx + dx, cy + dy) not in pos.values():
                plot(cx + dx, cy + dy, "·", _STYLE_SPARK)

    # Edges under each parent (skip when packed to one row per depth).
    if stride >= 2:
        for depth, level in enumerate(levels[:-1]):
            for aid in level:
                kids = [
                    child.id
                    for child in inv.children(aid)
                    if child.id in pos
                ]
                if not kids:
                    continue
                px, py = pos[aid]
                mid_y = py + 1
                kxs = [pos[k][0] for k in kids]
                lo, hi = min([px, *kxs]), max([px, *kxs])
                for x in range(lo, hi + 1):
                    plot(x, mid_y, "─", _STYLE_EDGE)
                for kx in kxs:
                    if kx == lo == hi:
                        ch = "│"
                    elif kx == lo:
                        ch = "┌"
                    elif kx == hi:
                        ch = "┐"
                    else:
                        ch = "┬"
                    plot(kx, mid_y, ch, _STYLE_EDGE)
                child_here = px in kxs
                if lo == hi:
                    plot(px, mid_y, "│", _STYLE_EDGE)
                elif px == lo:
                    plot(px, mid_y, "├" if child_here else "└", _STYLE_EDGE)
                elif px == hi:
                    plot(px, mid_y, "┤" if child_here else "┘", _STYLE_EDGE)
                else:
                    plot(px, mid_y, "┼" if child_here else "┴", _STYLE_EDGE)

    glyph = _SMALL if n > 16 else _NODE
    for aid, (x, y) in pos.items():
        action = inv.get_action(aid)
        is_root = aid == ROOT_ACTION_ID
        is_active = active_id is not None and aid == active_id
        ch = _ROOT if is_root else glyph
        if is_active:
            style = _STYLE_ACTIVE
        elif is_root:
            style = _STYLE_ROOT
        else:
            style = _STYLE_NODE
        plot(x, y, ch, style)

    if label_mode and stride >= 3:
        for aid, (x, y) in pos.items():
            action = inv.get_action(aid)
            label = (action.description or action.type).strip()[:8]
            ly = y + 2
            if ly >= height:
                continue
            start = max(0, min(x - len(label) // 2, width - len(label)))
            for i, lc in enumerate(label):
                plot(start + i, ly, lc, _STYLE_NODE)

    lines: list[str] = []
    for y in range(height):
        parts: list[str] = []
        run_style: Optional[str] = None
        run: list[str] = []

        def flush() -> None:
            if not run:
                return
            text = "".join(run).replace("[", "\\[")
            if run_style:
                parts.append(f"[{run_style}]{text}[/]")
            else:
                parts.append(text)

        for x in range(width):
            st = styles[y][x]
            if st != run_style:
                flush()
                run = [chars[y][x]]
                run_style = st
            else:
                run.append(chars[y][x])
        flush()
        lines.append("".join(parts).rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
