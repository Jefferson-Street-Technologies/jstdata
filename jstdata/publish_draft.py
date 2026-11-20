"""The publish draft: a whole publication's text as one Markdown document.

Instead of setting the title, description, overview and each query's note in
separate places, `jst publish` and `:publish` open one draft laid out like
the published page. The author fills it in, and `apply_draft` writes the
text back to the investigation before publishing.

Layout::

    # Title
    > One-line description

    Overview (Markdown, any length)

    ## Query 1: Name
    Note for query 1 (Markdown)

Lines inside ``<!-- -->`` are guidance and are ignored. Only ``## Query <id>:``
headings start a query section, so the overview and notes can use their own
``##`` headings freely.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from .investigation import Investigation
from .investigation.model import Query

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_QUERY_HEADING = re.compile(r"^## Query (\d+):[ \t]*(.*?)[ \t]*$", re.M)
_TITLE = re.compile(r"^# (?!#)(.*\S)[ \t]*$", re.M)

PLACEHOLDER_OVERVIEW = "What question does this answer, and what did you find?"
PLACEHOLDER_NOTE = "What should a reader notice in this chart?"


class DraftError(ValueError):
    """The draft can't be applied; the message says what to fix."""


def _describe(q: Query) -> str:
    """What a query's charts will show, for the guidance comment."""
    parts = []
    if q.metric:
        parts.append("metrics: " + ", ".join(q.metric))
    if q.entity:
        parts.append("entities: " + ", ".join(q.entity))
    if q.series:
        parts.append("series: " + ", ".join(q.series))
    if q.frequency:
        parts.append(q.frequency.lower())
    parts.append(f"first {q.head}" if q.head else f"last {q.tail or 20} observations")
    return " · ".join(parts)


def _web_note(web_versions: dict[str, str | None] | None, *keys: str) -> list[str]:
    """The page's version of fields edited in both places, as comments."""
    lines = []
    for key in keys:
        if web_versions and key in web_versions:
            text = (web_versions[key] or "(empty)").replace("-->", "-- >")
            lines.append(
                "<!-- Also edited on the page (yours above is kept; copy anything "
                f"you want from here):\n{text}\n-->"
            )
    return lines


def build_draft(
    inv: Investigation, web_versions: dict[str, str | None] | None = None
) -> str:
    """The publication as one editable Markdown document.

    ``web_versions`` maps fields changed both locally and on the page (see
    publish_sync) to the page's text, shown as a comment under each field.
    """
    lines = [
        "<!-- Fill this in, then press Ctrl+S to publish. It becomes the page's",
        "     text; the charts come from the queries. Markdown works: ## headings,",
        "     **bold**, - lists, [links](https://…). Lines like this are ignored. -->",
        "",
        f"# {inv.title}",
        f"> {inv.notes}" if inv.notes else "> One-line description shown under the title",
        *_web_note(web_versions, "title", "notes"),
        "",
        "<!-- Overview: shown above the charts. -->",
        inv.annotations or PLACEHOLDER_OVERVIEW,
        *_web_note(web_versions, "annotations"),
        "",
    ]
    for q in sorted(inv.queries.values(), key=lambda q: q.id):
        lines += [
            f"## Query {q.id}: {q.name or f'Query {q.id}'}",
            f"<!-- Charts: {_describe(q)} -->",
            q.annotations or PLACEHOLDER_NOTE,
            *_web_note(web_versions, f"query:{q.id}:name", f"query:{q.id}:annotations"),
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class DraftChanges:
    """What applying a draft changed, for a short confirmation message."""

    fields: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.fields)

    def summary(self) -> str:
        return ", ".join(self.fields) if self.fields else "no changes"


def _clean(text: str, placeholder: str) -> str | None:
    text = text.strip()
    return None if not text or text == placeholder else text


def apply_draft(inv: Investigation, draft: str) -> tuple[Investigation, DraftChanges]:
    """Read an edited draft back into a copy of ``inv``.

    Sections the author deleted leave that text unchanged (deleting a query's
    section never deletes the query). Unknown query ids are an error, so a
    typo can't silently drop a note.
    """
    text = _COMMENT.sub("", draft)
    if not text.strip():
        raise DraftError("The draft is empty, so nothing was published.")

    result = copy.deepcopy(inv)
    changes = DraftChanges()

    sections = list(_QUERY_HEADING.finditer(text))
    head = text[: sections[0].start()] if sections else text

    title = _TITLE.search(head)
    if title is None:
        raise DraftError('Add a title line starting with "# ".')
    if title.group(1).strip() != result.title:
        result.title = title.group(1).strip()
        changes.fields.append("title")
    after_title = head[title.end():]

    # The description: "> " lines right after the title.
    desc_lines = []
    body_lines = after_title.splitlines()
    i = 0
    while i < len(body_lines) and not body_lines[i].strip():
        i += 1
    while i < len(body_lines) and body_lines[i].startswith(">"):
        desc_lines.append(body_lines[i][1:].strip())
        i += 1
    description = _clean(" ".join(desc_lines), "One-line description shown under the title")
    if desc_lines and description != result.notes:
        result.notes = description
        changes.fields.append("description")

    overview = _clean("\n".join(body_lines[i:]), PLACEHOLDER_OVERVIEW)
    if overview != result.annotations:
        result.annotations = overview
        changes.fields.append("overview")

    for n, match in enumerate(sections):
        qid = int(match.group(1))
        if qid not in result.queries:
            known = ", ".join(str(k) for k in sorted(result.queries)) or "none"
            raise DraftError(
                f'"## Query {qid}:" doesn\'t match a query in this investigation '
                f"(its queries are {known})."
            )
        query = result.queries[qid]
        end = sections[n + 1].start() if n + 1 < len(sections) else len(text)
        name = match.group(2).strip() or None
        if name == f"Query {qid}":
            name = query.name  # the default heading, not a rename
        if name != query.name:
            query.name = name
            changes.fields.append(f"query {qid} name")
        note = _clean(text[match.end():end], PLACEHOLDER_NOTE)
        if note != query.annotations:
            query.annotations = note
            changes.fields.append(f"query {qid} note")

    return result, changes
