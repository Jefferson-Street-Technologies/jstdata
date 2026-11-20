"""Pulling web edits of a publication's text back into the local investigation.

A publication's text (title, description, overview, query names and notes)
can be edited in two places: locally before `jst publish --update`, and on
the page itself (JEF-166). Before publishing again, `jst` pulls the page's
text and merges it field by field against the text it last sent ("base"):

- changed only on the web  -> taken into the local investigation
- changed only locally     -> kept
- changed in both places   -> local kept; the web version is shown in the
                              draft as a comment so the author can merge it

Without a recorded base (links published before this existed), any field
that differs counts as changed in both places, so nothing is overwritten.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .investigation import Investigation

# Field keys: "title", "notes", "annotations", "query:<id>:name",
# "query:<id>:annotations".


def text_of(inv: Investigation | dict[str, Any]) -> dict[str, Optional[str]]:
    """The page text of an investigation (object or to_dict()), flattened."""
    data = inv.to_dict() if isinstance(inv, Investigation) else inv
    text: dict[str, Optional[str]] = {
        "title": data.get("title"),
        "notes": data.get("notes"),
        "annotations": data.get("annotations"),
    }
    for q in data.get("queries") or []:
        if isinstance(q, dict) and isinstance(q.get("id"), int):
            text[f"query:{q['id']}:name"] = q.get("name")
            text[f"query:{q['id']}:annotations"] = q.get("annotations") or q.get("notes")
    return text


def _norm(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _set(inv: Investigation, key: str, value: Optional[str]) -> None:
    if key == "title":
        inv.title = value or inv.title
    elif key == "notes":
        inv.notes = value
    elif key == "annotations":
        inv.annotations = value
    else:
        _, qid, attr = key.split(":")
        query = inv.queries.get(int(qid))
        if query is not None:
            setattr(query, attr, value)


LABELS = {"title": "title", "notes": "description", "annotations": "overview"}


def label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    _, qid, attr = key.split(":")
    return f"query {qid} {'name' if attr == 'name' else 'note'}"


@dataclass
class PullResult:
    investigation: Investigation
    pulled: list[str] = field(default_factory=list)
    #: key -> the web's text, for fields changed both locally and on the web
    conflicts: dict[str, Optional[str]] = field(default_factory=dict)


def pull_web_edits(
    inv: Investigation,
    remote_investigation: dict[str, Any],
    base: Optional[dict[str, Optional[str]]],
) -> PullResult:
    """Merge the page's text into a copy of ``inv`` (see module docstring)."""
    result = PullResult(copy.deepcopy(inv))
    local = text_of(inv)
    web = text_of(remote_investigation)
    for key, web_value in web.items():
        if key not in local:
            continue  # a query that no longer exists locally
        mine, theirs = _norm(local[key]), _norm(web_value)
        if mine == theirs:
            continue
        was = _norm(base.get(key)) if base is not None else None
        if base is not None and theirs != was and mine == was:
            _set(result.investigation, key, theirs)
            result.pulled.append(key)
        elif base is not None and theirs == was:
            continue  # only changed locally
        else:
            result.conflicts[key] = theirs
    return result


def pull_for_publish(
    inv: Investigation, previous: dict[str, Any]
) -> PullResult:
    """Fetch ``previous``'s page and merge its text into a copy of ``inv``.

    Raises PublishError if the page can't be fetched; callers can publish
    without pulling in that case.
    """
    from .publish import fetch_publication, parse_publication_ref

    from .publish import PublishError

    origin, pub_id = parse_publication_ref(previous["url"])
    remote = fetch_publication(origin, pub_id)
    # A page older than our own last update is a stale copy (e.g. a cache):
    # merging against it would wrongly look like "no web edits".
    ours, theirs = _when(previous.get("updated_at")), _when(remote.get("updatedAt"))
    if ours is not None and (theirs is None or theirs < ours):
        raise PublishError(
            "The page returned an out-of-date copy of its text. Try again in a moment."
        )
    return pull_web_edits(inv, remote["investigation"], previous.get("text"))


def _when(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp from the server or our own records.

    Python 3.10's fromisoformat only takes 3 or 6 fractional digits, and
    Postgres trims trailing zeros (".2", ".24049"), so pad to 6 first.
    """
    if not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
