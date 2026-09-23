from datetime import datetime

import pytest

from jstdata.models import Entity, Observation, Series, TimeSeries
from jstdata.workflows.base import get_step, resolve_pipeline
from jstdata.workflows.chart import (
    format_compact,
    format_rank_line,
    horizontal_bar,
    last_observation_value,
    pick_entity,
    shared_pane_max,
)


def _ts(sid, entities, values, freq="Annual", units="USD"):
    series = Series(
        id=sid,
        label=sid,
        frequency=freq,
        source="test",
        units=units,
        seasonal_adjustment="",
        last_updated=datetime(2024, 1, 1),
        metric_id="gdp",
        entities=[Entity(id=e, label=e.title()) for e in entities],
    )
    obs = [
        Observation(
            series_id=sid,
            observation_timestamp=datetime(2020 + i, 1, 1),
            release_timestamp=datetime(2020 + i, 1, 1),
            value=v,
        )
        for i, v in enumerate(values)
    ]
    return TimeSeries(series=series, observations=obs)


def test_pick_entity_first_without_taxonomy():
    ents = [Entity(id="us", label="United States"), Entity(id="ny", label="New York")]
    assert pick_entity(ents).id == "us"
    assert pick_entity([]) is None


def test_pick_entity_prefers_taxonomy_member():
    ents = [Entity(id="ny", label="New York"), Entity(id="us", label="United States")]
    assert pick_entity(ents, {"us", "de"}).id == "us"
    assert pick_entity(ents, {"ca"}).id == "ny"


def test_last_observation_value_is_chronological():
    ts = _ts("a", ["us"], [1.0, 9.0, 3.0])
    assert last_observation_value(ts) == 3.0
    empty = _ts("b", ["us"], [])
    assert last_observation_value(empty) is None


def test_shared_pane_max_and_bar():
    assert shared_pane_max([1.0, 4.0, 2.0]) == 4.0
    assert shared_pane_max([]) == 1.0
    assert shared_pane_max([-1.0, -2.0]) == 1.0
    bar = horizontal_bar(0.5, 8)
    assert len(bar) == 8
    assert "█" in bar
    assert horizontal_bar(0.0, 8) == " " * 8


def test_format_rank_line_shared_scale():
    line = format_rank_line(1, "United States", 100.0, 100.0, bar_width=10)
    assert "United States" in line or "United State" in line
    assert format_compact(100.0) in line
    assert "█" in line
    short = format_rank_line(2, "X", 50.0, 100.0, bar_width=10)
    assert short.count("█") < line.count("█")


def test_rank_is_registered():
    import jstdata.workflows  # noqa: F401

    spec = get_step("rank")
    assert spec.name == "Rank"
    assert "rank" in spec.to_dict()["example"]
    assert any(a["name"] == "taxonomy" for a in spec.to_dict()["arguments"])
    keys = {b["key"] for b in spec.to_dict()["bindings"]}
    assert "/" in keys
    assert "enter" in keys
    assert "tab" in keys


def test_resolve_rank_taxonomy():
    resolved = resolve_pipeline(["rank", "--taxonomy", "country"])
    assert resolved[0].spec.id == "rank"
    assert resolved[0].kwargs["taxonomy"] == "country"

    defaulted = resolve_pipeline(["rank"])
    assert defaulted[0].kwargs.get("taxonomy") in (None, "")

    chained = resolve_pipeline(
        ["discover", ":", "rank", "--taxonomy", "country"]
    )
    assert [r.spec.id for r in chained] == ["discover", "rank"]
    assert chained[1].kwargs["taxonomy"] == "country"


def test_resolve_rank_session_entities():
    import jstdata.workflows  # noqa: F401

    defaulted = resolve_pipeline(["rank"])
    assert defaulted[0].kwargs.get("session_entities") is True

    flag_off = resolve_pipeline(["rank", "--session-entities", "false"])
    assert flag_off[0].kwargs["session_entities"] is False

    combined = resolve_pipeline(
        ["rank", "--taxonomy", "country", "--session-entities", "true"]
    )
    assert combined[0].kwargs["taxonomy"] == "country"
    assert combined[0].kwargs["session_entities"] is True

    spec = get_step("rank")
    arg = next(a for a in spec.to_dict()["arguments"] if a["name"] == "session_entities")
    assert arg["default"] is True


def test_rank_scopes_to_session_entities_when_present():
    from unittest.mock import MagicMock

    from jstdata.session import Session
    from jstdata.workflows.rank import RankScreen

    with_entities = RankScreen(
        MagicMock(), Session(entity=["france", "germany"]), session_entities=True
    )
    assert with_entities._using_session_entities()
    assert with_entities._scoped_entity_ids() == ["france", "germany"]

    no_entities = RankScreen(MagicMock(), Session(metric=["gdp"]), session_entities=True)
    assert not no_entities._using_session_entities()
    assert no_entities._scoped_entity_ids() is None

    opted_out = RankScreen(
        MagicMock(), Session(entity=["france"]), session_entities=False
    )
    assert not opted_out._using_session_entities()
    assert opted_out._scoped_entity_ids() is None
