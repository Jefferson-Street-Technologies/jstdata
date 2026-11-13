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
        inspector="Latest filings and economic data updates, newest first.",
        filters="Newest first · 20 per page",
    ),
    Destination(
        id=DEST_SEARCH,
        number="02",
        title="Search",
        blurb="Find financial and economic data",
        inspector="Find metrics, entities, and series.",
        filters="All types · Metrics · Entities · Series",
    ),
    Destination(
        id=DEST_ACTIONS,
        number="03",
        title="Actions",
        blurb="Explore investigation relationships",
        inspector="Views opened in this investigation.",
        filters="Outline · Resume a view",
    ),
    Destination(
        id=DEST_WORKSPACE,
        number="04",
        title="Workspace",
        blurb="Current working set",
        inspector="Current working set.",
        filters="Metrics · Entities · Series",
    ),
    Destination(
        id=DEST_QUERIES,
        number="05",
        title="Queries",
        blurb="Saved observation queries",
        inspector="Saved observation queries.",
        filters="Create · View · Delete",
    ),
)

_DEST_BY_ID = {d.id: d for d in DESTINATIONS}


def get_destination(dest_id: str) -> Destination:
    try:
        return _DEST_BY_ID[dest_id]
    except KeyError as exc:
        raise KeyError(f"Unknown destination {dest_id!r}") from exc


def annotations_panel(
    text: str | None,
    *,
    empty_hint: str = "Press a to add.",
) -> str:
    """Inspector block for investigation / query annotations."""
    body = (text or "").strip()
    if not body:
        return f"[#888]No annotations. {empty_hint}[/]"
    return f"[bold #888]ANNOTATIONS[/]\n{body}"


def workspace_counts_line(inv: Investigation) -> str:
    """Compact bag + query counts for the Home header (3M · 4E · 1Q)."""
    ws = inv.workspace
    return (
        f"{len(ws.metric)}M · {len(ws.entity)}E · {len(ws.series)}S · "
        f"{len(inv.queries)}Q"
    )


def is_fresh_investigation(inv: Investigation) -> bool:
    """True when a new user would still be staring at an empty bag."""
    ws = inv.workspace
    resources = len(ws.metric) + len(ws.entity) + len(ws.series)
    return resources <= 1 and not inv.queries and len(inv.actions) <= 1


def inspector_copy(dest: Destination, inv: Investigation) -> str:
    """Structured inspector panel — hierarchy, not documentation."""
    ws = inv.workspace
    nq = len(inv.queries)
    nodes = len(inv.actions)

    if dest.id == DEST_RECENT:
        body = (
            f"{dest.inspector}\n\n"
            "[bold #888]BROWSE[/]\n"
            "Newest first                 Enter to open\n"
            "20 per page\n\n"
            "Id, title, date, and source."
        )
    elif dest.id == DEST_SEARCH:
        body = (
            f"{dest.inspector}\n\n"
            "[bold #888]BROWSE[/]\n"
            "All types                    Enter to search\n"
            "Metrics\n"
            "Entities\n"
            "Series\n\n"
            "[bold #888]WORKSPACE[/]\n"
            f"{len(ws.metric)} metrics · {len(ws.entity)} entities · "
            f"{len(ws.series)} series"
        )
    elif dest.id == DEST_ACTIONS:
        label = "node" if nodes == 1 else "nodes"
        body = (
            f"{dest.inspector}\n\n"
            "[bold #888]OUTLINE[/]\n"
            f"{nodes} {label}                     Enter to resume"
        )
    elif dest.id == DEST_WORKSPACE:
        body = (
            f"{dest.inspector}\n\n"
            "[bold #888]BAG[/]\n"
            f"{len(ws.metric)} metrics\n"
            f"{len(ws.entity)} entities\n"
            f"{len(ws.series)} series\n\n"
            "[#888]Enter to inspect · Backspace to remove[/]"
        )
    else:
        label = "query" if nq == 1 else "queries"
        body = (
            f"{dest.inspector}\n\n"
            "[bold #888]SAVED[/]\n"
            f"{nq} {label}                      Enter to open\n"
            "Create from workspace"
        )

    body += "\n\n" + annotations_panel(
        inv.annotations, empty_hint="Press a to annotate this investigation."
    )

    if is_fresh_investigation(inv):
        body += (
            "\n\n[bold #4ade80]Start exploring[/]\n"
            "[#aaa]Search for a company, metric, or economic series. "
            "Add resources to your workspace, then create a query.[/]"
        )
    return body


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
        if spec.name == "rename":
            items.append(
                PaletteItem(
                    label="Rename investigation",
                    detail=spec.summary,
                    line="rename",
                    kind="command",
                )
            )
            continue
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
# Action graph visual — orthogonal left/right tree
# ---------------------------------------------------------------------------

_NODE = "●"
_SMALL = "•"
_STYLE_ROOT = "bold #052e16 on #4ade80"
_STYLE_NODE = "bold #4ade80"
_STYLE_LEAF = "#86efac"
_STYLE_ACTIVE = "bold #052e16 on #86efac"
_STYLE_EDGE = "#4ade80"


def _stack_offsets(n: int) -> list[int]:
    """Row offsets centered on the parent. Even counts skip the parent row."""
    if n <= 0:
        return []
    if n == 1:
        return [0]
    if n % 2 == 1:
        half = n // 2
        return list(range(-half, half + 1))
    half = n // 2
    return list(range(-half, 0)) + list(range(1, half + 1))


def _split_root_children(kids: list) -> tuple[list, list]:
    left, right = [], []
    for i, kid in enumerate(kids):
        (right if i % 2 == 0 else left).append(kid)
    return left, right


def _tree_depth(inv: Investigation, aid: int = ROOT_ACTION_ID) -> int:
    kids = list(inv.children(aid))
    if not kids:
        return 0
    return 1 + max(_tree_depth(inv, child.id) for child in kids)


def _orthogonal_positions(
    inv: Investigation, width: int, height: int
) -> dict[int, tuple[int, int]]:
    """Root at center; children go left or right; same-side siblings stack."""
    cx, cy = width // 2, height // 2
    pos: dict[int, tuple[int, int]] = {ROOT_ACTION_ID: (cx, cy)}
    hops = max(_tree_depth(inv), 1)
    gap = max(4, min((width // 2 - 2) // hops, 10))

    def clamp_x(x: int) -> int:
        return max(1, min(width - 2, x))

    def clamp_y(y: int) -> int:
        return max(0, min(height - 1, y))

    def place_stack(nodes: list, x: int, parent_y: int, side: int) -> None:
        x = clamp_x(x)
        used: set[int] = set()
        for node, off in zip(nodes, _stack_offsets(len(nodes))):
            y = clamp_y(parent_y + off)
            if y in used:
                for candidate in range(height):
                    down, up = y + candidate, y - candidate
                    if 0 <= down < height and down not in used:
                        y = down
                        break
                    if 0 <= up < height and up not in used:
                        y = up
                        break
            used.add(y)
            pos[node.id] = (x, y)
            grand = list(inv.children(node.id))
            if grand:
                place_stack(grand, x + side * gap, y, side)

    kids = list(inv.children(ROOT_ACTION_ID))
    left, right = _split_root_children(kids)
    if right:
        place_stack(right, cx + gap, cy, +1)
    if left:
        place_stack(left, cx - gap, cy, -1)
    return pos


def render_action_graph(
    inv: Investigation,
    *,
    width: int = 56,
    height: int = 11,
    active_id: Optional[int] = None,
) -> str:
    """Dots and orthogonal limbs from the center. No rings, no diagonals."""
    width = max(int(width), 16)
    height = max(int(height), 5)
    if ROOT_ACTION_ID not in inv.actions:
        return ""

    pos = _orthogonal_positions(inv, width, height)
    n = len(pos)

    chars = [[" " for _ in range(width)] for _ in range(height)]
    styles: list[list[Optional[str]]] = [[None] * width for _ in range(height)]

    def plot(x: int, y: int, ch: str, style: Optional[str]) -> None:
        if 0 <= x < width and 0 <= y < height:
            chars[y][x] = ch
            styles[y][x] = style

    def hline(x0: int, x1: int, y: int) -> None:
        if x0 == x1:
            return
        step = 1 if x1 > x0 else -1
        for x in range(x0 + step, x1, step):
            plot(x, y, "─", _STYLE_EDGE)

    def vline(x: int, y0: int, y1: int) -> None:
        lo, hi = min(y0, y1), max(y0, y1)
        for y in range(lo, hi + 1):
            plot(x, y, "│", _STYLE_EDGE)

    for aid, (px, py) in pos.items():
        kids = [c for c in inv.children(aid) if c.id in pos]
        if not kids:
            continue
        by_x: dict[int, list[int]] = {}
        for child in kids:
            child_x, child_y = pos[child.id]
            by_x.setdefault(child_x, []).append(child_y)
        for child_x, ys in by_x.items():
            hline(px, child_x, py)
            vline(child_x, min(ys + [py]), max(ys + [py]))

    glyph = _SMALL if n > 18 else _NODE
    for aid, (x, y) in pos.items():
        is_active = active_id is not None and aid == active_id
        is_leaf = not any(c.id in pos for c in inv.children(aid))
        if aid == ROOT_ACTION_ID:
            style = _STYLE_ACTIVE if is_active else _STYLE_ROOT
            plot(x, y, _NODE, style)
            continue
        if is_active:
            style = _STYLE_ACTIVE
        elif is_leaf:
            style = _STYLE_LEAF
        else:
            style = _STYLE_NODE
        plot(x, y, glyph, style)

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
