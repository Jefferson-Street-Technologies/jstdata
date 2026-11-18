import pytest
import requests
import requests_mock
from datetime import datetime, timezone

from jstdata.client import (
    ApiKeyNotSetError,
    InvalidApiKeyError,
    InvalidInputError,
    JSTDataClient
)
from jstdata.models import (
    Series,
    Entity,
    Metric,
    Observation,
    PreviewRow,
    Release,
    Taxonomy,
    TimeSeries,
)


@pytest.fixture
def mock_url():
    """Fixture for the base URL of the Jefferson Street API."""
    return "https://api.jeffersonst.io"

@pytest.fixture(autouse=True)
def no_local_config(monkeypatch, tmp_path):
    # Set APP_DIR to a temp path to avoid touching user's home dir during tests
    monkeypatch.setattr("jstdata.client.APP_DIR", tmp_path)
    r = lambda *args, **kwargs: {"api_key": "test_api_key", "base_url": "https://api.jeffersonst.io"}
    w = lambda *args, **kwargs: None
    monkeypatch.setattr("jstdata.client.JSTDataClientConfig.read", r)
    monkeypatch.setattr("jstdata.client.JSTDataClientConfig.write", w)


@pytest.fixture
def client(mock_url):
    """Fixture for JSTDataClient with a dummy API key."""
    cli = JSTDataClient(api_key="test_api_key", base_url=mock_url)
    return cli


def test_client_init_no_api_key(monkeypatch, tmp_path):
    """Test that ApiKeyNotSetError is raised when no API key is provided."""
    monkeypatch.setattr("jstdata.client.APP_DIR", tmp_path)
    monkeypatch.setattr("jstdata.client.CONFIG_FILE", tmp_path / "config.json")
    # Ensure config read returns no api key
    monkeypatch.setattr("jstdata.client.JSTDataClientConfig.read", lambda *args, **kwargs: {})
    
    with pytest.raises(ApiKeyNotSetError):
        cli = JSTDataClient(api_key=None)
        _ = cli.api_key


def test_make_request_invalid_api_key(client, mock_url):
    """Test failed API key validation (403)."""
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/some-endpoint", status_code=403)
        with pytest.raises(InvalidApiKeyError):
            client.make_request("some-endpoint")


def test_get_series(client, mock_url):
    """Test get_series method."""
    mock_data = {
        "id": "ABC123",
        "label": "Test Series",
        "frequency": "Monthly",
        "source": "Test Source",
        "units": "Test Units",
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "last_updated": "2024-01-01 00:00:00",
        "metric_slug": "test-metric",
        "entities": [{"id": "usa", "label": "United States"}]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/series/ABC123", json=mock_data)
        series = client.get_series("ABC123")
        assert isinstance(series, Series)
        assert series.id == "ABC123"
        assert series.entities[0].id == "usa"


def _query_record():
    return {
        "id": "ABC123",
        "label": "GDP USA",
        "frequency": "Quarterly",
        "source": "BEA",
        "units": "USD",
        "seasonal_adjustment": "",
        "last_updated": "2024-01-01T00:00:00",
        "metric_id": "gdp",
        "entities": [{"id": "usa", "label": "USA"}],
        "observations": [
            {
                "observation_timestamp": "2024-01-01T00:00:00",
                "release_timestamp": "2024-01-01T00:00:00",
                "release_id": "bea-2024-01-01",
                "value": 100.0,
            }
        ],
    }


def test_query(client, mock_url):
    """Test query method defaults to tail and returns TimeSeries."""
    mock_data = {"records": [_query_record()]}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/query", json=mock_data)
        results = client.query(metric="gdp", entity="usa")
        assert len(results) == 1
        assert isinstance(results[0], TimeSeries)
        assert results[0].series.id == "ABC123"
        assert results[0].observations[0].value == 100.0
        assert results[0].observations[0].release_id == "bea-2024-01-01"
        qs = m.request_history[-1].qs
        assert qs["tail"] == ["20"]
        assert "head" not in qs
        assert "start_date" not in qs


def test_query_limit_offset(client, mock_url):
    mock_data = {"records": []}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/query", json=mock_data)
        results = client.query(metric="gdp", entity="usa", limit=50, offset=10)
        assert results == []
        qs = m.request_history[-1].qs
        assert qs["limit"] == ["50"]
        assert qs["offset"] == ["10"]
        assert qs["tail"] == ["20"]


def test_query_rejects_head_and_tail(client):
    with pytest.raises(InvalidInputError, match="head"):
        client.query(metric="gdp", head=10, tail=10)


def test_query_rejects_invalid_sort_by(client):
    with pytest.raises(InvalidInputError, match="sort_by"):
        client.query(metric="gdp", sort_by="mean")


def test_query_as_of(client, mock_url):
    mock_data = {"records": []}
    as_of = datetime(2020, 3, 1, tzinfo=timezone.utc)
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/query", json=mock_data)
        client.query(metric="gdp", tail=1, as_of=as_of)
        qs = m.request_history[-1].qs
        assert qs["tail"] == ["1"]
        assert "as_of" in qs


def test_query_sort_by_value_and_taxonomy(client, mock_url):
    mock_data = {"records": []}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/query", json=mock_data)
        client.query(
            metric="gdp",
            taxonomy="country",
            frequency="Annual",
            tail=1,
            sort_by="value",
            limit=50,
            offset=0,
        )
        qs = m.request_history[-1].qs
        assert qs["taxonomy"] == ["country"]
        assert qs["frequency"] == ["annual"]  # requests_mock lowercases values
        assert qs["tail"] == ["1"]
        assert qs["sort_by"] == ["value"]
        assert qs["limit"] == ["50"]
        assert qs["offset"] == ["0"]


def test_query_df(client, mock_url):
    """Test query_df flattens nested TimeSeries observations."""
    mock_data = {"records": [_query_record()]}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/query", json=mock_data)
        df = client.query_df(metric="gdp")
        import pandas as pd
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert df.iloc[0]["value"] == 100.0
        assert df.iloc[0]["series_id"] == "ABC123"
        assert df.iloc[0]["entity_id"] == "usa"
        assert df.iloc[0]["release_id"] == "bea-2024-01-01"


def test_get_series_observations(client, mock_url):
    mock_data = {
        "series_id": "ABC123",
        "limit": 1000,
        "offset": 0,
        "observations": [
            {
                "observation_timestamp": "2024-01-01T00:00:00",
                "release_timestamp": "2024-01-01T00:00:00",
                "release_id": "bea-2024-01-01",
                "value": 100.0,
            }
        ],
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/series/ABC123/observations", json=mock_data)
        results = client.get_series_observations(
            "ABC123",
            start_date="2000-01-01",
            release_id="bea-2024-01-01",
            limit=1000,
        )
        assert len(results) == 1
        assert isinstance(results[0], Observation)
        assert results[0].series_id == "ABC123"
        assert results[0].value == 100.0
        assert results[0].release_id == "bea-2024-01-01"
        qs = m.request_history[-1].qs
        assert qs["start_date"] == ["2000-01-01"]
        assert qs["limit"] == ["1000"]
        assert qs["release_id"] == ["bea-2024-01-01"]


def test_get_metric_fat_fields(client, mock_url):
    mock_data = {
        "id": "long-term-debt",
        "name": "Long-term Debt",
        "description": "Obligations due beyond one year.",
        "variants": [
            {
                "frequency": "Annual",
                "source": "10-K",
                "seasonal_adjustment": None,
                "units": "USD",
                "series_count": 4,
            }
        ],
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/metric/long-term-debt", json=mock_data)
        metric = client.get_metric("long-term-debt")
        assert isinstance(metric, Metric)
        assert metric.description.startswith("Obligations")
        assert len(metric.variants) == 1
        assert metric.variants[0].series_count == 4


def test_preview_and_release(client, mock_url):
    preview_data = {
        "records": [
            {
                "metric_id": "long-term-debt",
                "entity_id": "cik:1652044",
                "series_count": 1,
                "observation_count": 12,
                "earliest": "2015-01-01T00:00:00",
                "latest": "2025-01-01T00:00:00",
            }
        ]
    }
    release_data = {
        "id": "0001652044-25-000012",
        "label": "10-K (2025-02-04)",
        "release_date": "2025-02-04",
        "source": "10-K",
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/preview", json=preview_data)
        m.get(f"{mock_url}/release/0001652044-25-000012", json=release_data)
        rows = client.preview(
            metric="long-term-debt", entity="cik:1652044", limit=10
        )
        assert len(rows) == 1
        assert isinstance(rows[0], PreviewRow)
        assert rows[0].series_count == 1
        release = client.get_release("0001652044-25-000012")
        assert isinstance(release, Release)
        assert release.source == "10-K"
        assert release.release_date.isoformat() == "2025-02-04"


def test_list_releases(client, mock_url):
    payload = {
        "records": [
            {
                "id": "bea-2024-02-01",
                "label": "GDP (2024-02-01)",
                "release_date": "2024-02-01",
                "source": "BEA",
            }
        ]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/release", json=payload)
        rows = client.list_releases(
            limit=20,
            offset=0,
            sort_order="desc",
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert len(rows) == 1
        assert rows[0].id == "bea-2024-02-01"
        assert rows[0].label == "GDP (2024-02-01)"
        qs = m.last_request.qs
        assert qs["limit"] == ["20"]
        assert qs["offset"] == ["0"]
        assert qs["sort_order"] == ["desc"]
        assert qs["start_date"] == ["2024-01-01"]
        assert qs["end_date"] == ["2024-12-31"]


def test_preview_requires_metric_and_entity(client):
    with pytest.raises(InvalidInputError, match="preview"):
        client.preview(metric=["long-term-debt"])


def test_list_taxonomies(client, mock_url):
    mock_data = {
        "records": [
            {"id": "sec-central-index-key", "name": "SEC Central Index Key"}
        ]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/taxonomy", json=mock_data)
        taxonomies = client.list_taxonomies()
        assert len(taxonomies) == 1
        assert isinstance(taxonomies[0], Taxonomy)
        assert taxonomies[0].id == "sec-central-index-key"


def test_get_taxonomy_entities(client, mock_url):
    mock_data = {
        "records": [{"id": "cik:320193", "label": "Apple Inc."}]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/taxonomy/sec-central-index-key/entities", json=mock_data)
        entities = client.get_taxonomy_entities("sec-central-index-key")
        assert entities[0].id == "cik:320193"


def test_search_entities_taxonomy(client, mock_url):
    mock_data = {
        "records": [{"id": "california", "label": "California"}]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/search/entities", json=mock_data)
        entities = client.search_entities("calif", taxonomy="us-state-or-territory")
        assert entities[0].id == "california"
        qs = m.request_history[-1].qs
        assert qs["taxonomy"] == ["us-state-or-territory"]
        assert qs["query"] == ["calif"]
        assert "mode" not in qs


def test_search_entities_relation(client, mock_url):
    mock_data = {
        "records": [{"id": "california", "label": "California"}]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/search/entities", json=mock_data)
        entities = client.search_entities(relation="part_of:united-states")
        assert entities[0].id == "california"
        qs = m.request_history[-1].qs
        assert qs["relation"] == ["part_of:united-states"]
        assert "query" not in qs


def test_search_entities_multiple_relations(client, mock_url):
    mock_data = {
        "records": [{"id": "cik:1045810", "label": "NVIDIA CORP"}]
    }
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/search/entities", json=mock_data)
        entities = client.search_entities(
            relation=["classified_as:sic:3674", "has_security:ticker:NVDA"]
        )
        assert entities[0].id == "cik:1045810"
        # requests_mock lowercases query values
        qs = m.request_history[-1].qs
        assert sorted(qs["relation"]) == sorted(
            ["classified_as:sic:3674", "has_security:ticker:nvda"]
        )


def test_search_metrics_omits_blank_query(client, mock_url):
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/search/metrics", json={"records": []})
        client.search_metrics("", entity="united-states", limit=100)
        qs = m.request_history[-1].qs
        assert "query" not in qs
        assert qs["entity"] == ["united-states"]
        assert qs["mode"] == ["union"]
        assert qs["limit"] == ["100"]


def test_search_metrics_multiple_entities_and_mode(client, mock_url):
    mock_data = {"records": [{"id": "gdp", "name": "GDP"}]}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/search/metrics", json=mock_data)
        metrics = client.search_metrics(
            entity=["france", "germany"],
            mode="intersect",
            limit=100,
            offset=20,
        )
        assert metrics[0].id == "gdp"
        qs = m.request_history[-1].qs
        assert "query" not in qs
        assert qs["entity"] == ["france", "germany"]
        assert qs["mode"] == ["intersect"]
        assert qs["offset"] == ["20"]


def test_search_entities_multiple_metrics(client, mock_url):
    mock_data = {"records": [{"id": "france", "label": "France"}]}
    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/search/entities", json=mock_data)
        entities = client.search_entities(
            "paris",
            metric=["gdp", "cpi"],
            mode="union",
        )
        assert entities[0].id == "france"
        qs = m.request_history[-1].qs
        assert qs["query"] == ["paris"]
        assert qs["metric"] == ["cpi", "gdp"] or qs["metric"] == ["gdp", "cpi"]
        assert qs["mode"] == ["union"]


def test_get_resources_batches_over_api_limit(client, mock_url):
    """``GET /resource`` caps at 30 ids; client must chunk larger sessions."""
    from jstdata.client import MAX_RESOURCE_IDS

    ids = [f"id-{i}" for i in range(MAX_RESOURCE_IDS + 5)]
    with requests_mock.Mocker() as m:
        def _responder(request, context):
            requested = request.qs.get("resource", [])
            assert len(requested) <= MAX_RESOURCE_IDS
            return {
                "records": [{"id": rid, "label": rid.upper()} for rid in requested]
            }

        m.get(f"{mock_url}/resource", json=_responder)
        resources = client.get_resources(ids)
        assert len(m.request_history) == 2
        assert [r.id for r in resources] == ids
        assert resources[0].label == "ID-0"


@pytest.mark.parametrize(
    "method, path",
    [
        ("search", "search"),
        ("search_entities", "search/entities"),
        ("search_metrics", "search/metrics"),
        ("search_series", "search/series"),
    ],
)
def test_search_lowercases_query(client, mock_url, method, path):
    """The API only matches lowercase text; ``Amazon`` must be sent as ``amazon``."""
    from urllib.parse import parse_qs, urlparse

    with requests_mock.Mocker() as m:
        m.get(f"{mock_url}/{path}", json={"records": []})
        getattr(client, method)("  Amazon ")
        # requests_mock's .qs lowercases values, so read the raw URL.
        raw = parse_qs(urlparse(m.request_history[-1].url).query)
        assert raw["query"] == ["amazon"]
