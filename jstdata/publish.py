"""Publish & Clone: share an investigation as a web page, or copy one locally.

``jst publish`` sends an investigation's definition to the Jefferson Street
website, which runs its queries and serves the results at ``/p/<id>``.
``jst clone`` downloads a published definition and saves it as a new local
investigation. See the website's ``lib/publications`` for the server side.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

import requests

from .investigation import (
    Investigation,
    InvestigationError,
    investigation_exists,
    save_investigation,
    validate_investigation_id,
)
from . import client as _client
from .investigation.model import ROOT_ACTION_ID

if TYPE_CHECKING:
    from .client import JSTDataClient

DEFAULT_SITE_URL = "https://www.jeffersonst.io"
# Publishing runs every query server-side before it answers.
PUBLISH_TIMEOUT_S = 120
CLONE_TIMEOUT_S = 30

_PUBLICATION_ID = re.compile(r"^[A-Za-z0-9]{10}$")
# A definition is at most 256 KB on the server; refuse anything far bigger.
MAX_CLONE_BYTES = 1024 * 1024
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def check_site_url(site_url: str) -> str:
    """The site URL without a trailing slash, if it's safe to send a key to.

    Publishing sends your API key, so plain http is only allowed for a site
    on this machine.
    """
    parsed = urlparse(site_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise PublishError(
            f"{site_url!r} isn't a website address. Use something like "
            f"{DEFAULT_SITE_URL}."
        )
    if parsed.scheme == "http" and parsed.hostname not in (
        h.strip("[]") for h in _LOCAL_HOSTS
    ):
        raise PublishError(
            f"{site_url} uses plain http, which would send your API key "
            "unencrypted. Use https."
        )
    return site_url.rstrip("/")


def configured_site_url(client: "JSTDataClient") -> str:
    """Website for publish/clone: JSTDATA_SITE_URL > config site_url > default."""
    try:
        configured = client._cfg.read().get("site_url")
    except Exception:
        configured = None
    return os.environ.get("JSTDATA_SITE_URL") or configured or DEFAULT_SITE_URL


class PublishError(Exception):
    """A publish or clone failed; the message is meant for the user."""


class QueriesChangedError(PublishError):
    """An in-place update was refused because the queries changed."""


def _unreachable(site_url: str, exc: requests.RequestException) -> PublishError:
    """A short, actionable message instead of urllib3's connection trace."""
    if isinstance(exc, requests.Timeout):
        return PublishError(f"{site_url} took too long to answer. Try again.")
    return PublishError(
        f"Couldn't reach {site_url}. Check your connection, or that the site "
        "is running if it's a local address."
    )


def _error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"{response.status_code} {response.reason}"


def publish_investigation(
    inv: Investigation,
    *,
    api_key: str,
    site_url: str = DEFAULT_SITE_URL,
    publisher_name: Optional[str] = None,
) -> dict[str, Any]:
    """Publish ``inv`` and return ``{"id", "url", "recordCount"}``.

    The server runs the queries with ``api_key``, so they count toward the
    key's usage like any other query. Each call creates a new publication;
    earlier ones are never changed.
    """
    from .ops.curation import publication_projection

    projected = publication_projection(inv)
    if not projected.get("queries"):
        raise PublishError(
            f"{inv.id!r} has no report queries to publish. "
            "Save one with `:query` / `jst query add`, or "
            "`jst actions promote --query <id>`."
        )
    site_url = check_site_url(site_url)
    body: dict[str, Any] = {"investigation": projected}
    if publisher_name:
        body["publisherName"] = publisher_name
    try:
        response = requests.post(
            f"{site_url}/api/publications",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=PUBLISH_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise _unreachable(site_url, exc) from exc
    if response.status_code != 201:
        raise PublishError(_error_message(response))
    try:
        result = response.json()
        if not isinstance(result.get("url"), str):
            raise ValueError("no url")
    except (ValueError, AttributeError) as exc:
        raise PublishError(
            f"{site_url} answered, but not with a publication link."
        ) from exc
    return result


def update_publication(
    inv: Investigation,
    publication_id: str,
    *,
    api_key: str,
    site_url: str = DEFAULT_SITE_URL,
    publisher_name: Optional[str] = None,
) -> dict[str, Any]:
    """Replace an existing publication's text with ``inv``'s (JEF-166).

    Updates the title, description, overview and query names and notes on
    the same link. The results aren't rerun, so the queries must be
    unchanged; if they changed the server refuses with
    :class:`QueriesChangedError`, and the author should publish a new link.
    Only the publication's own publisher can update it.
    """
    from .ops.curation import publication_projection

    site_url = check_site_url(site_url)
    body: dict[str, Any] = {"investigation": publication_projection(inv)}
    if publisher_name:
        body["publisherName"] = publisher_name
    try:
        response = requests.patch(
            f"{site_url}/api/publications/{publication_id}",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=PUBLISH_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise _unreachable(site_url, exc) from exc
    if response.status_code == 409:
        raise QueriesChangedError(_error_message(response))
    if response.status_code == 404:
        raise PublishError(
            f"Publication {publication_id!r} wasn't found, or wasn't published "
            "with your account."
        )
    if response.status_code != 200:
        raise PublishError(_error_message(response))
    try:
        result = response.json()
        if not isinstance(result.get("url"), str):
            raise ValueError("no url")
    except (ValueError, AttributeError) as exc:
        raise PublishError(
            f"{site_url} answered, but not with a publication link."
        ) from exc
    return result


# --- Which links each local investigation was published to ----------------

_LOG_FILE = "publications.json"


def _log_path(app_dir: Optional[Path]) -> Path:
    # Looked up at call time, so tests that patch client.APP_DIR are isolated.
    return (app_dir if app_dir is not None else _client.APP_DIR) / _LOG_FILE


def _read_log(app_dir: Optional[Path]) -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(_log_path(app_dir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_publication(
    investigation_id: str,
    result: dict[str, Any],
    *,
    text: Optional[dict[str, Any]] = None,
    app_dir: Optional[Path] = None,
) -> None:
    """Remember that ``investigation_id`` lives at this link, newest first.

    ``text`` is the page text that was sent (see ``publish_sync.text_of``),
    and the server's ``updatedAt`` is kept with it: together they let the
    next publish tell web edits apart from local ones and pull them.
    """
    log = _read_log(app_dir)
    entries = [
        e
        for e in log.get(investigation_id, [])
        if isinstance(e, dict) and e.get("id") != result.get("id")
    ]
    entry: dict[str, Any] = {"id": result["id"], "url": result["url"]}
    if text is not None:
        entry["text"] = text
        entry["updated_at"] = result.get("updatedAt")
    entries.insert(0, entry)
    log[investigation_id] = entries[:20]
    path = _log_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2))


def last_publication(
    investigation_id: str, *, app_dir: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    """The link this investigation was most recently published or updated at."""
    for entry in _read_log(app_dir).get(investigation_id, []):
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and _PUBLICATION_ID.match(entry["id"])
            and isinstance(entry.get("url"), str)
        ):
            return entry
    return None


def parse_publication_ref(
    ref: str, *, site_url: str = DEFAULT_SITE_URL
) -> tuple[str, str]:
    """Split a publication URL (or bare id) into ``(site_url, id)``.

    A full URL keeps its own origin, so clones work from any deployment.
    """
    ref = ref.strip()
    if _PUBLICATION_ID.match(ref):
        return site_url.rstrip("/"), ref
    parsed = urlparse(ref)
    match = re.match(r"^/p/([A-Za-z0-9]{10})/?$", parsed.path)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not match:
        raise PublishError(
            f"{ref!r} isn't a publication link. Expected something like "
            f"{DEFAULT_SITE_URL}/p/AbCdEf1234."
        )
    return f"{parsed.scheme}://{parsed.netloc}", match.group(1)


def _id_taken(candidate: str, root: Optional[Path]) -> bool:
    """True when ``candidate`` is already used as an investigation or project id."""
    if root is not None:
        return investigation_exists(candidate, root=root)
    from .investigation.project_store import find_investigation_project, project_exists

    return (
        find_investigation_project(candidate) is not None
        or project_exists(candidate)
        or investigation_exists(candidate, root=root)
    )


def _available_id(base: str, root: Optional[Path]) -> str:
    """``base`` if free locally, else ``base-clone``, ``base-clone-2``, ..."""
    if not _id_taken(base, root):
        return base
    candidate = f"{base}-clone"
    n = 2
    while _id_taken(candidate, root):
        candidate = f"{base}-clone-{n}"
        n += 1
    return candidate


def fetch_publication(origin: str, pub_id: str) -> dict[str, Any]:
    """A publication's stored definition: ``{"investigation", "updatedAt", …}``.

    Public, so no API key is sent. The reply comes from another server, so its
    size is capped and its shape checked.
    """
    try:
        response = requests.get(
            f"{origin}/api/publications/{pub_id}/definition",
            timeout=CLONE_TIMEOUT_S,
            stream=True,
        )
        if response.status_code == 404:
            raise PublishError(f"No publication {pub_id!r} at {origin}.")
        if response.status_code != 200:
            raise PublishError(_error_message(response))
        # The definition comes from another server: cap what we read.
        raw = response.raw.read(MAX_CLONE_BYTES + 1, decode_content=True)
    except requests.RequestException as exc:
        raise _unreachable(origin, exc) from exc
    if len(raw) > MAX_CLONE_BYTES:
        raise PublishError(f"The publication at {origin} is too large to clone.")
    try:
        body = json.loads(raw)
        data = body["investigation"]
        if not isinstance(data, dict) or not isinstance(data["id"], str):
            raise TypeError("investigation")
    except (ValueError, KeyError, TypeError) as exc:
        raise PublishError(
            f"{origin} didn't send a valid investigation for {pub_id!r}."
        ) from exc
    return body


def clone_publication(
    ref: str,
    *,
    site_url: str = DEFAULT_SITE_URL,
    root: Optional[Path] = None,
    new_id: Optional[str] = None,
) -> Investigation:
    """Download a publication's definition and save it as a new investigation.

    The local copy is independent: editing or running it never touches the
    publication. If the original id is taken locally, a ``-clone`` suffix is
    added unless ``new_id`` is given.
    """
    origin, pub_id = parse_publication_ref(ref, site_url=site_url)
    data = dict(fetch_publication(origin, pub_id)["investigation"])
    published_id = data["id"]

    if new_id is not None:
        validate_investigation_id(new_id)
        if _id_taken(new_id, root):
            raise InvestigationError(f"Investigation {new_id!r} already exists.")
        data["id"] = new_id
    else:
        try:
            validate_investigation_id(published_id)
        except InvestigationError as exc:
            raise PublishError(
                f"The published id {published_id!r} isn't a valid local id. "
                "Choose one with --as, e.g. --as my-copy."
            ) from exc
        data["id"] = _available_id(published_id, root)

    # Older definitions or hand-built ones may lack the TUI's view tree;
    # an investigation needs at least its root action.
    if not data.get("actions"):
        data["actions"] = [
            {"id": ROOT_ACTION_ID, "type": "home", "args": {}},
        ]

    inv = Investigation.from_dict(data)
    if root is not None:
        # Explicit root (tests / legacy callers): flat YAML under that directory.
        save_investigation(inv, root=root)
        return inv

    from .investigation.project import Project
    from .investigation.project_store import save_project_investigation

    project = Project.new(
        inv.id,
        title=inv.title or inv.id,
        notes=inv.notes,
        investigation_ids=[inv.id],
    )
    save_project_investigation(inv.id, inv, project=project)
    return inv
