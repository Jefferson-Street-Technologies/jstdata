"""Tests for the Workspace model."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jstdata.investigation.model import Workspace


def test_to_query_kwargs_omits_empty_and_none():
    ws = Workspace(metric=["gdp"], entity=["usa"])
    assert ws.to_query_kwargs() == {
        "metric": ["gdp"],
        "entity": ["usa"],
    }


def test_to_query_kwargs_includes_filters():
    ws = Workspace(
        metric=["gdp"],
        frequency="Annual",
        taxonomy="country",
        tail=12,
        as_of="2020-03-01T00:00:00Z",
        sort_by="value",
        order_by="desc",
        start_date="2020-01-01",
        end_date="2024-01-01",
    )
    assert ws.to_query_kwargs() == {
        "metric": ["gdp"],
        "frequency": "Annual",
        "taxonomy": "country",
        "tail": 12,
        "as_of": "2020-03-01T00:00:00Z",
        "sort_by": "value",
        "order_by": "desc",
    }


def test_from_dict_requires_id():
    ws = Workspace.from_dict(
        {
            "id": 0,
            "metric": ["gdp"],
            "entity": ["usa"],
            "series": ["s1"],
            "frequency": "Monthly",
        }
    )
    assert ws.id == 0
    assert ws.metric == ["gdp"]
    assert ws.entity == ["usa"]
    assert ws.series == ["s1"]
    assert ws.frequency == "Monthly"


def test_is_empty_and_resource_ids():
    empty = Workspace()
    assert empty.is_empty()
    assert empty.resource_ids() == []

    ws = Workspace(metric=["a"], entity=["b"], series=["c"])
    assert not ws.is_empty()
    assert ws.resource_ids() == ["a", "b", "c"]


def test_add_and_remove():
    ws = Workspace()
    assert ws.add_metric("gdp") is True
    assert ws.add_metric("gdp") is False
    assert ws.add_entity("usa") is True
    assert ws.remove_id("gdp") is True
    assert ws.metric == []
    assert ws.entity == ["usa"]
    assert ws.remove_id("missing") is False


def test_to_cli_and_python():
    ws = Workspace(
        metric=["gdp"],
        entity=["usa"],
        taxonomy="country",
        tail=1,
        sort_by="value",
    )
    cli = ws.to_cli()
    assert cli.startswith("jst api query")
    assert "--metric gdp" in cli
    assert "--entity usa" in cli
    assert "--taxonomy country" in cli
    assert "--tail 1" in cli
    assert "--sort-by value" in cli

    py = ws.to_python()
    assert "JSTDataClient" in py
    assert "query_df" in py
    assert "gdp" in py


def test_to_python_empty():
    py = Workspace().to_python()
    assert "query_df()" in py or "query_df(\n)" in py


def test_execute_and_execute_df():
    ws = Workspace(metric=["gdp"], entity=["usa"])
    client = MagicMock()
    client.query.return_value = ["ts"]
    client.query_df.return_value = "df"
    assert ws.execute(client) == ["ts"]
    assert ws.execute(client, order_by="desc") == ["ts"]
    client.query.assert_called_with(
        metric=["gdp"], entity=["usa"], order_by="desc"
    )
    assert ws.execute_df(client) == "df"


def test_to_csv(tmp_path: Path):
    from datetime import datetime

    from jstdata.models import Entity, Observation, Series, TimeSeries

    series = Series(
        id="s1",
        label="GDP USA",
        frequency="Annual",
        source="BEA",
        units="USD",
        seasonal_adjustment="",
        last_updated=datetime(2024, 1, 1),
        metric_id="gdp",
        entities=[Entity(id="usa", label="USA")],
    )
    ts = TimeSeries(
        series=series,
        observations=[
            Observation(
                series_id="s1",
                observation_timestamp=datetime(2024, 1, 1),
                release_timestamp=datetime(2024, 1, 1),
                value=100.0,
            )
        ],
    )

    class FakeClient:
        def query(self, **kwargs):
            return [ts]

    out = tmp_path / "out.csv"
    ws = Workspace(metric=["gdp"], entity=["usa"])
    path = ws.to_csv(FakeClient(), path=out)
    assert path == out
    text = out.read_text()
    assert "GDP USA" in text
    assert "100.0" in text
