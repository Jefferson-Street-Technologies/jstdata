"""jst publish / jst clone (JEF-141) against a mocked website."""

import json

import pytest
from click.testing import CliRunner

from jstdata.investigation import (
    Investigation,
    investigation_exists,
    load_investigation,
    save_investigation,
)
from jstdata.investigation.model import Query
from jstdata.publish import (
    PublishError,
    clone_publication,
    parse_publication_ref,
    publish_investigation,
)

SITE = "https://site.test"
PUB_ID = "AbCdEf1234"


@pytest.fixture
def inv() -> Investigation:
    i = Investigation.new("plain-bagel", title="Plain Bagel", notes="Hyperscaler debt")
    i.annotations = "Why debt matters."
    i.queries[1] = Query(
        id=1,
        name="Latest debt",
        annotations="Sorted by value.",
        metric=["long-term-debt"],
        entity=["cik:1652044"],
        tail=1,
        sort_by="value",
    )
    return i


def published_definition(inv: Investigation) -> dict:
    """What the website stores: known fields, queries normalised with nulls."""
    d = inv.to_dict()
    for q in d["queries"]:
        for key in ("name", "annotations", "frequency", "taxonomy", "head", "tail",
                    "as_of", "sort_by", "order_by", "units", "source", "seasonal_adjustment"):
            q.setdefault(key, None)
    return d


def test_publish_sends_definition_with_bearer_key(requests_mock, inv):
    requests_mock.post(
        f"{SITE}/api/publications",
        status_code=201,
        json={"id": PUB_ID, "url": f"{SITE}/p/{PUB_ID}", "recordCount": 4},
    )
    result = publish_investigation(inv, api_key="k123", site_url=SITE + "/", publisher_name="Nik")

    assert result["url"] == f"{SITE}/p/{PUB_ID}"
    req = requests_mock.last_request
    assert req.headers["Authorization"] == "Bearer k123"
    # The key goes in the header only, never in the URL or body.
    assert "k123" not in req.url and "k123" not in req.text
    body = req.json()
    assert body["publisherName"] == "Nik"
    assert body["investigation"]["id"] == "plain-bagel"
    assert body["investigation"]["queries"][0]["annotations"] == "Sorted by value."


def test_publish_surfaces_server_error(requests_mock, inv):
    requests_mock.post(
        f"{SITE}/api/publications",
        status_code=422,
        json={"error": "Query 1 was rejected by the API: Unknown metric", "queryId": 1},
    )
    with pytest.raises(PublishError, match="Query 1 was rejected"):
        publish_investigation(inv, api_key="k", site_url=SITE)


def test_publish_refuses_investigation_without_queries():
    with pytest.raises(PublishError, match="no queries"):
        publish_investigation(Investigation.new("empty"), api_key="k", site_url=SITE)


@pytest.mark.parametrize(
    "ref,expected",
    [
        (PUB_ID, (SITE, PUB_ID)),
        (f"https://other.test/p/{PUB_ID}", ("https://other.test", PUB_ID)),
        (f"http://localhost:3000/p/{PUB_ID}/", ("http://localhost:3000", PUB_ID)),
    ],
)
def test_parse_publication_ref(ref, expected):
    assert parse_publication_ref(ref, site_url=SITE) == expected


@pytest.mark.parametrize("ref", ["nope", f"{SITE}/q/{PUB_ID}", f"ftp://x/p/{PUB_ID}", f"{SITE}/p/short"])
def test_parse_publication_ref_rejects_other_links(ref):
    with pytest.raises(PublishError):
        parse_publication_ref(ref, site_url=SITE)


def test_clone_saves_independent_copy(requests_mock, tmp_path, inv):
    requests_mock.get(
        f"{SITE}/api/publications/{PUB_ID}/definition",
        json={"id": PUB_ID, "investigation": published_definition(inv)},
    )
    cloned = clone_publication(f"{SITE}/p/{PUB_ID}", root=tmp_path)

    assert cloned.id == "plain-bagel"
    loaded = load_investigation("plain-bagel", root=tmp_path)
    assert loaded.title == "Plain Bagel"
    assert loaded.notes == "Hyperscaler debt"
    assert loaded.annotations == "Why debt matters."
    q = loaded.queries[1]
    assert (q.name, q.annotations, q.tail, q.sort_by) == ("Latest debt", "Sorted by value.", 1, "value")
    assert q.entity == ["cik:1652044"]


def test_clone_picks_a_free_id_when_taken(requests_mock, tmp_path, inv):
    save_investigation(inv, root=tmp_path)  # the user's own copy
    requests_mock.get(
        f"{SITE}/api/publications/{PUB_ID}/definition",
        json={"investigation": published_definition(inv)},
    )
    first = clone_publication(PUB_ID, site_url=SITE, root=tmp_path)
    second = clone_publication(PUB_ID, site_url=SITE, root=tmp_path)
    assert (first.id, second.id) == ("plain-bagel-clone", "plain-bagel-clone-2")
    # The existing local investigation is untouched.
    assert load_investigation("plain-bagel", root=tmp_path).queries[1].name == "Latest debt"


def test_clone_adds_root_action_when_missing(requests_mock, tmp_path, inv):
    definition = published_definition(inv)
    definition.pop("actions")
    definition.pop("workspace")
    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", json={"investigation": definition})
    cloned = clone_publication(PUB_ID, site_url=SITE, root=tmp_path, new_id="my-copy")
    assert cloned.id == "my-copy"
    assert investigation_exists("my-copy", root=tmp_path)


def test_clone_missing_publication(requests_mock, tmp_path):
    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", status_code=404, json={"error": "Not found"})
    with pytest.raises(PublishError, match="No publication"):
        clone_publication(PUB_ID, site_url=SITE, root=tmp_path)


def test_cli_publish_and_clone(requests_mock, tmp_path, monkeypatch, inv):
    from jstdata import cli as cli_module
    from jstdata.investigation import active, store

    # Keep the user's real ~/.jstdata untouched: investigations, the active
    # pointer that clone sets, and the config publish reads.
    monkeypatch.setattr(store, "INVESTIGATIONS_DIR", tmp_path / "investigations")
    monkeypatch.setattr(active, "APP_DIR", tmp_path)
    monkeypatch.setenv("JSTDATA_SITE_URL", SITE)
    monkeypatch.setattr(type(cli_module.client), "api_key", property(lambda self: "k123"))
    save_investigation(inv, root=tmp_path / "investigations")

    requests_mock.post(
        f"{SITE}/api/publications",
        status_code=201,
        json={"id": PUB_ID, "url": f"{SITE}/p/{PUB_ID}", "recordCount": 1},
    )
    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["publish", "plain-bagel"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Not published." in result.output
    assert not requests_mock.called

    result = runner.invoke(cli_module.cli, ["publish", "plain-bagel", "--yes"])
    assert result.exit_code == 0, result.output
    assert f"{SITE}/p/{PUB_ID}" in result.output
    assert "Latest debt" in result.output

    requests_mock.get(
        f"{SITE}/api/publications/{PUB_ID}/definition",
        json={"investigation": published_definition(inv)},
    )
    result = runner.invoke(cli_module.cli, ["clone", f"{SITE}/p/{PUB_ID}"])
    assert result.exit_code == 0, result.output
    assert "plain-bagel-clone" in result.output
    assert active.load_active().investigation == "plain-bagel-clone"


def test_unreachable_site_gets_a_short_message(requests_mock, inv):
    import requests

    requests_mock.post(f"{SITE}/api/publications", exc=requests.ConnectionError("pool trace"))
    with pytest.raises(PublishError) as excinfo:
        publish_investigation(inv, api_key="k", site_url=SITE)
    message = str(excinfo.value)
    assert message.startswith(f"Couldn't reach {SITE}.")
    assert "pool trace" not in message
