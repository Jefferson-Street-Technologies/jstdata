import json

import pytest
import requests_mock
from click.testing import CliRunner
from jstdata.cli import cli
from jstdata.client import JSTDataClient


@pytest.fixture
def runner():
    """Fixture for CliRunner."""
    return CliRunner()


@pytest.fixture
def mock_url():
    """Fixture for the base URL of the Jefferson Street API."""
    return "https://api.jeffersonst.io"


@pytest.fixture(autouse=True)
def mock_client(monkeypatch, mock_url, tmp_path):
    # Set APP_DIR to a temp path
    monkeypatch.setattr("jstdata.client.APP_DIR", tmp_path)
    client = JSTDataClient(api_key="testing", base_url=mock_url)
    monkeypatch.setattr("jstdata.cli.client", client)
    return client


def test_cli_help(runner):
    """Test the main CLI help message."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Jefferson Street CLI - A Research OS for Financial Data." in result.output
    assert "api" in result.output
    assert "action" in result.output
    assert "investigation" in result.output


def test_metric_ls(runner, mock_url):
    """Test 'api metric ls' command."""
    mock_data = {"records": [{"id": "gdp", "name": "Gross Domestic Product"}]}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/metric", json=mock_data)
        result = runner.invoke(cli, ["api", "metric", "ls"])
        assert result.exit_code == 0
        assert "gdp" in result.output
        assert "Gross Domestic Product" in result.output


def test_taxonomy_ls(runner, mock_url):
    mock_data = {
        "records": [
            {"id": "sec-central-index-key", "name": "SEC Central Index Key"}
        ]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/taxonomy", json=mock_data)
        result = runner.invoke(cli, ["api", "taxonomy", "ls"])
        assert result.exit_code == 0
        assert "sec-central-index-key" in result.output
        assert "SEC Central Index Key" in result.output


def test_query_direct_id(runner, mock_url):
    """Test 'api query' command with direct IDs."""
    mock_query_data = {
        "records": [
            {
                "id": "ABC123",
                "label": "GDP USA",
                "frequency": "Quarterly",
                "source": "BEA",
                "units": "USD",
                "last_updated": "2024-01-01T00:00:00",
                "metric_id": "gdp",
                "entities": [{"id": "usa", "label": "USA"}],
                "observations": [
                    {
                        "observation_timestamp": "2024-01-01T00:00:00",
                        "release_timestamp": "2024-01-01T00:00:00",
                        "value": 100.0,
                    }
                ],
            }
        ]
    }
    with requests_mock.Mocker() as m:
        m.get(
            f"{mock_url}/search/metrics",
            json={"records": [{"id": "gdp", "name": "GDP"}]},
        )
        m.get(
            f"{mock_url}/search/entities",
            json={"records": [{"id": "usa", "label": "USA"}]},
        )
        m.get(f"{mock_url}/query", json=mock_query_data)

        result = runner.invoke(
            cli,
            ["api", "query", "--metric", "gdp", "--entity", "usa", "--limit", "50"],
        )
        assert result.exit_code == 0, result.output
        assert "ABC123" in result.output
        assert "100.0" in result.output
        query_req = [r for r in m.request_history if "/query" in r.url][-1]
        assert query_req.qs["limit"] == ["50"]
        assert query_req.qs["tail"] == ["20"]


def test_query_fuzzy_resolution(runner, mock_url):
    """Test 'api query' with fuzzy resolution of keywords."""
    mock_query_data = {
        "records": [
            {
                "id": "ABC123",
                "label": "GDP USA",
                "last_updated": "2024-01-01T00:00:00",
                "entities": [{"id": "usa", "label": "USA"}],
                "observations": [
                    {
                        "observation_timestamp": "2024-01-01T00:00:00",
                        "release_timestamp": "2024-01-01T00:00:00",
                        "value": 100.0,
                    }
                ],
            }
        ]
    }
    with requests_mock.Mocker() as m:
        m.get(
            f"{mock_url}/search/metrics?query=inflation&limit=1",
            json={"records": [{"id": "cpi", "name": "CPI"}]},
        )
        m.get(
            f"{mock_url}/search/entities?query=america&limit=1",
            json={"records": [{"id": "usa", "label": "USA"}]},
        )
        m.get(f"{mock_url}/query", json=mock_query_data)

        result = runner.invoke(
            cli, ["api", "query", "--metric", "inflation", "--entity", "america"]
        )
        assert result.exit_code == 0
        assert m.request_history[-1].qs["metric"] == ["cpi"]
        assert m.request_history[-1].qs["entity"] == ["usa"]
        assert m.request_history[-1].qs["tail"] == ["20"]


def test_query_sort_by_value_and_taxonomy(runner, mock_url):
    mock_query_data = {"records": []}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/query", json=mock_query_data)
        result = runner.invoke(
            cli,
            [
                "api",
                "query",
                "--metric",
                "gdp",
                "--taxonomy",
                "country",
                "--frequency",
                "Annual",
                "--tail",
                "1",
                "--sort-by",
                "value",
                "--limit",
                "50",
            ],
        )
        assert result.exit_code == 0, result.output
        qs = m.request_history[-1].qs
        assert qs["metric"] == ["gdp"]
        assert qs["taxonomy"] == ["country"]
        assert qs["frequency"] == ["annual"]
        assert qs["tail"] == ["1"]
        assert qs["sort_by"] == ["value"]
        assert qs["limit"] == ["50"]


def test_series_observations(runner, mock_url):
    mock_data = {
        "series_id": "ABC123",
        "limit": 100,
        "offset": 0,
        "observations": [
            {
                "observation_timestamp": "2024-01-01T00:00:00",
                "release_timestamp": "2024-01-01T00:00:00",
                "value": 100.0,
            }
        ],
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/series/ABC123/observations", json=mock_data)
        result = runner.invoke(
            cli,
            [
                "api",
                "series",
                "observations",
                "ABC123",
                "--start-date",
                "2000-01-01",
                "--limit",
                "100",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "100.0" in result.output
        qs = m.request_history[-1].qs
        assert qs["start_date"] == ["2000-01-01"]
        assert qs["limit"] == ["100"]


def test_action_ls(runner):
    result = runner.invoke(cli, ["action", "ls"])
    assert result.exit_code == 0
    assert "console" in result.output
    assert "discover" in result.output


def test_action_ls_json(runner):
    result = runner.invoke(cli, ["action", "ls", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert any(s["id"] == "console" for s in data)
    assert any(s["id"] == "discover" for s in data)


def test_action_show(runner):
    result = runner.invoke(cli, ["action", "show", "console"])
    assert result.exit_code == 0
    assert "Console" in result.output
    assert "jst action add console" in result.output
    assert "--resource-type" in result.output


def test_action_show_discover(runner):
    result = runner.invoke(cli, ["action", "show", "discover"])
    assert result.exit_code == 0
    assert "Discover" in result.output
    assert "--mode" in result.output
    assert "space" in result.output


def test_action_show_json(runner):
    result = runner.invoke(cli, ["action", "show", "console", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "console"
    assert any(a["name"] == "taxonomy" for a in data["arguments"])
    assert any(a["name"] == "relation" for a in data["arguments"])
    assert any(a["name"] == "resource_type" for a in data["arguments"])
    assert any(b["key"] == "/" for b in data["bindings"])


def test_action_show_unknown(runner):
    result = runner.invoke(cli, ["action", "show", "nope"])
    assert result.exit_code == 1
    assert "Unknown action" in result.output


def test_actions_plural_removed(runner):
    result = runner.invoke(cli, ["actions"])
    assert result.exit_code != 0


def test_run_removed(runner):
    result = runner.invoke(cli, ["run", "console"])
    assert result.exit_code != 0


def test_workflow_removed(runner):
    result = runner.invoke(cli, ["workflow", "ls"])
    assert result.exit_code != 0


def test_metric_search_listing_and_entities(runner, mock_url):
    with requests_mock.Mocker() as m:
        m.get(
            f"{mock_url}/search/metrics",
            json={"records": [{"id": "gdp", "name": "GDP"}]},
        )
        result = runner.invoke(
            cli,
            [
                "api",
                "metric",
                "search",
                "--entity",
                "france",
                "--entity",
                "germany",
                "--mode",
                "intersect",
                "--limit",
                "100",
            ],
        )
        assert result.exit_code == 0, result.output
        qs = m.request_history[-1].qs
        assert "query" not in qs
        assert qs["entity"] == ["france", "germany"]
        assert qs["mode"] == ["intersect"]
        assert qs["limit"] == ["100"]
        assert "GDP" in result.output
