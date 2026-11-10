from datetime import datetime

import pytest

from jstdata.models import Entity, Observation, Series, TimeSeries
from jstdata.actions.base import get_action, parse_action_kwargs
from jstdata.actions.chart import (
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
    import jstdata.actions  # noqa: F401

    spec = get_action("rank")
    assert spec.name == "Rank"
    assert "rank" in spec.to_dict()["example"]
    assert any(a["name"] == "taxonomy" for a in spec.to_dict()["arguments"])
    keys = {b["key"] for b in spec.to_dict()["bindings"]}
    assert "/" in keys
    assert "enter" in keys
    assert "tab" in keys


def test_parse_rank_taxonomy():
    spec = get_action("rank")
    resolved = parse_action_kwargs(spec, ["--taxonomy", "country"])
    assert resolved["taxonomy"] == "country"

    defaulted = parse_action_kwargs(spec, [])
    assert defaulted.get("taxonomy") in (None, "")


def test_parse_rank_workspace_entities():
    import jstdata.actions  # noqa: F401

    spec = get_action("rank")
    defaulted = parse_action_kwargs(spec, [])
    assert defaulted.get("workspace_entities") is True

    flag_off = parse_action_kwargs(spec, ["--workspace-entities", "false"])
    assert flag_off["workspace_entities"] is False

    combined = parse_action_kwargs(
        spec, ["--taxonomy", "country", "--workspace-entities", "true"]
    )
    assert combined["taxonomy"] == "country"
    assert combined["workspace_entities"] is True

    arg = next(a for a in spec.to_dict()["arguments"] if a["name"] == "workspace_entities")
    assert arg["default"] is True


def test_rank_scopes_to_workspace_entities_when_present():
    from unittest.mock import MagicMock

    from jstdata.investigation.model import Workspace
    from jstdata.actions.rank import RankScreen

    with_entities = RankScreen(
        MagicMock(), Workspace(entity=["france", "germany"]), workspace_entities=True
    )
    assert with_entities._using_workspace_entities()
    assert with_entities._scoped_entity_ids() == ["france", "germany"]

    no_entities = RankScreen(MagicMock(), Workspace(metric=["gdp"]), workspace_entities=True)
    assert not no_entities._using_workspace_entities()
    assert no_entities._scoped_entity_ids() is None

    opted_out = RankScreen(
        MagicMock(), Workspace(entity=["france"]), workspace_entities=False
    )
    assert not opted_out._using_workspace_entities()
    assert opted_out._scoped_entity_ids() is None
