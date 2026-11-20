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
    # Remembered publication links (publications.json) go here too.
    monkeypatch.setattr("jstdata.client.APP_DIR", tmp_path)
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


# --- Hardening (review of PR #6) -------------------------------------------


@pytest.mark.parametrize(
    "body,kwargs",
    [
        ({"text": "<html>oops</html>"}, {}),
        ({"json": {"no": "investigation"}}, {}),
        ({"json": {"investigation": ["a", "list"]}}, {}),
        ({"json": {"investigation": {"title": "no id"}}}, {}),
        ({"json": {"investigation": {"id": 42}}}, {}),
    ],
)
def test_clone_rejects_malformed_replies(requests_mock, tmp_path, body, kwargs):
    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", **body)
    with pytest.raises(PublishError, match="didn't send a valid investigation"):
        clone_publication(PUB_ID, site_url=SITE, root=tmp_path, **kwargs)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "Housing", "abc\n"])
def test_clone_refuses_unsafe_published_ids(requests_mock, tmp_path, inv, bad_id):
    definition = published_definition(inv)
    definition["id"] = bad_id
    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", json={"investigation": definition})
    with pytest.raises(PublishError, match="--as"):
        clone_publication(PUB_ID, site_url=SITE, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_clone_refuses_unsafe_as_id(requests_mock, tmp_path, inv):
    from jstdata.investigation import InvestigationError

    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", json={"investigation": published_definition(inv)})
    with pytest.raises(InvestigationError):
        clone_publication(PUB_ID, site_url=SITE, root=tmp_path, new_id="../evil")


def test_clone_never_sends_an_api_key(requests_mock, tmp_path, inv, monkeypatch):
    monkeypatch.setenv("JSTDATA_API_KEY", "secret-key")
    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", json={"investigation": published_definition(inv)})
    clone_publication(f"{SITE}/p/{PUB_ID}", root=tmp_path)
    req = requests_mock.last_request
    assert "Authorization" not in req.headers
    assert "secret-key" not in req.url


def test_clone_caps_response_size(requests_mock, tmp_path):
    from jstdata.publish import MAX_CLONE_BYTES

    requests_mock.get(f"{SITE}/api/publications/{PUB_ID}/definition", content=b" " * (MAX_CLONE_BYTES + 10))
    with pytest.raises(PublishError, match="too large"):
        clone_publication(PUB_ID, site_url=SITE, root=tmp_path)


def test_publish_refuses_plain_http_except_locally(requests_mock, inv):
    with pytest.raises(PublishError, match="unencrypted"):
        publish_investigation(inv, api_key="k", site_url="http://example.com")
    assert not requests_mock.called
    with pytest.raises(PublishError, match="isn't a website address"):
        publish_investigation(inv, api_key="k", site_url="www.jeffersonst.io")

    requests_mock.post("http://localhost:3000/api/publications", status_code=201, json={"id": PUB_ID, "url": "u"})
    assert publish_investigation(inv, api_key="k", site_url="http://localhost:3000")["url"] == "u"


def test_publish_rejects_reply_without_link(requests_mock, inv):
    requests_mock.post(f"{SITE}/api/publications", status_code=201, json={"id": PUB_ID})
    with pytest.raises(PublishError, match="not with a publication link"):
        publish_investigation(inv, api_key="k", site_url=SITE)


def test_config_set_site_url(tmp_path, monkeypatch):
    import json

    from jstdata import cli as cli_module
    from jstdata import client as client_module

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"api_key": "file-key"}))
    monkeypatch.setattr(client_module, "CONFIG_FILE", config_file)
    monkeypatch.setenv("JSTDATA_API_KEY", "env-only-key")
    runner = CliRunner()

    result = runner.invoke(cli_module.cli, ["config", "set", "site_url", "http://example.com"])
    assert result.exit_code == 2 and "unencrypted" in result.output

    result = runner.invoke(cli_module.cli, ["config", "set", "site_url", "https://dev.example.com/"])
    assert result.exit_code == 0, result.output
    saved = json.loads(config_file.read_text())
    # Only site_url changed: the env-only key was not copied to disk.
    assert saved == {"api_key": "file-key", "site_url": "https://dev.example.com"}


# --- Updating a publication in place (JEF-166) ------------------------------


def test_update_sends_text_with_bearer_key(requests_mock, inv):
    from jstdata.publish import update_publication

    requests_mock.patch(
        f"{SITE}/api/publications/{PUB_ID}",
        json={"id": PUB_ID, "url": f"{SITE}/p/{PUB_ID}", "updatedAt": "2026-11-20T12:00:00Z"},
    )
    result = update_publication(inv, PUB_ID, api_key="k123", site_url=SITE)
    assert result["url"] == f"{SITE}/p/{PUB_ID}"
    req = requests_mock.last_request
    assert req.method == "PATCH"
    assert req.headers["Authorization"] == "Bearer k123"
    assert req.json()["investigation"]["title"] == "Plain Bagel"


def test_update_refused_when_queries_changed(requests_mock, inv):
    from jstdata.publish import QueriesChangedError, update_publication

    requests_mock.patch(
        f"{SITE}/api/publications/{PUB_ID}",
        status_code=409,
        json={"error": "The queries changed since this was published.", "code": "queries_changed"},
    )
    with pytest.raises(QueriesChangedError, match="queries changed"):
        update_publication(inv, PUB_ID, api_key="k", site_url=SITE)


def test_update_of_someone_elses_publication(requests_mock, inv):
    from jstdata.publish import update_publication

    requests_mock.patch(f"{SITE}/api/publications/{PUB_ID}", status_code=404, json={"error": "Not found"})
    with pytest.raises(PublishError, match="wasn't published with your account"):
        update_publication(inv, PUB_ID, api_key="k", site_url=SITE)


def test_remembered_links_newest_first(tmp_path):
    from jstdata.publish import last_publication, record_publication

    assert last_publication("x", app_dir=tmp_path) is None
    record_publication("x", {"id": "AAAAAAAAAA", "url": "u1"}, app_dir=tmp_path)
    record_publication("x", {"id": "BBBBBBBBBB", "url": "u2"}, app_dir=tmp_path)
    assert last_publication("x", app_dir=tmp_path)["id"] == "BBBBBBBBBB"
    # Updating an older link moves it back to the top.
    record_publication("x", {"id": "AAAAAAAAAA", "url": "u1"}, app_dir=tmp_path)
    assert last_publication("x", app_dir=tmp_path)["id"] == "AAAAAAAAAA"
    # A corrupt file is treated as empty, not an error.
    (tmp_path / "publications.json").write_text("not json")
    assert last_publication("x", app_dir=tmp_path) is None


def test_cli_publish_then_update(requests_mock, tmp_path, monkeypatch, inv):
    from jstdata import cli as cli_module
    from jstdata.investigation import active, store

    monkeypatch.setattr(store, "INVESTIGATIONS_DIR", tmp_path / "investigations")
    monkeypatch.setattr(active, "APP_DIR", tmp_path)
    monkeypatch.setattr("jstdata.client.APP_DIR", tmp_path)
    monkeypatch.setenv("JSTDATA_SITE_URL", SITE)
    monkeypatch.setattr(type(cli_module.client), "api_key", property(lambda self: "k123"))
    save_investigation(inv, root=tmp_path / "investigations")
    runner = CliRunner()

    result = runner.invoke(cli_module.cli, ["publish", "plain-bagel", "--update", "--yes"])
    assert result.exit_code == 2 and "hasn't been published" in result.output

    requests_mock.post(f"{SITE}/api/publications", status_code=201,
                       json={"id": PUB_ID, "url": f"{SITE}/p/{PUB_ID}", "recordCount": 1})
    result = runner.invoke(cli_module.cli, ["publish", "plain-bagel", "--yes"])
    assert result.exit_code == 0, result.output

    requests_mock.patch(f"{SITE}/api/publications/{PUB_ID}",
                        json={"id": PUB_ID, "url": f"{SITE}/p/{PUB_ID}", "updatedAt": "t"})
    result = runner.invoke(cli_module.cli, ["publish", "plain-bagel", "--update", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Updated." in result.output
    assert requests_mock.last_request.method == "PATCH"
