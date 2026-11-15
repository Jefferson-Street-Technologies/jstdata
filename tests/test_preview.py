"""Tests for ``jst preview`` data resolution and HTML rendering."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from jstdata.cli import cli
from jstdata.investigation.model import Investigation, InvestigationError
from jstdata.investigation.store import save_investigation
from jstdata.models import Entity, Metric, Observation, Series
from jstdata.preview.data import (
    ChartModel,
    PreviewModel,
    QueryPanel,
    SeriesLine,
    build_preview_model,
    most_recent_investigation_id,
    pick_primary_query,
    resolve_investigation_id,
)
from jstdata.preview.render import render_html


def _series(
    sid: str,
    entity_id: str,
    label: str,
    metric_id: str = "percentage-of-foreign-bank-assets-among-total-bank-assets",
) -> Series:
    return Series(
        id=sid,
        label=f"Metric for {label}",
        frequency="Annual",
        source="Global Financial Development",
        units="Percent",
        seasonal_adjustment="NSA",
        last_updated=datetime(2022, 1, 1),
        metric_id=metric_id,
        entities=[Entity(id=entity_id, label=label)],
    )


def _obs(sid: str, year: int, value: float) -> Observation:
    return Observation(
        series_id=sid,
        observation_timestamp=datetime(year, 1, 1),
        release_timestamp=datetime(year, 6, 1),
        value=value,
        release_id="rel",
    )


def _chart(**kwargs) -> ChartModel:
    defaults = dict(
        metric_id="m",
        metric_name="Percentage of Something",
        series_id="S1",
        source="World Bank, Global Financial Development Database",
        source_display="World Bank (S1)",
        entity_count=2,
        lines=[
            SeriesLine(
                entity_id="b",
                entity_label="Bangladesh",
                series_id="Sb",
                observations=[(datetime(2024, 1, 1), 5.1)],
            ),
            SeriesLine(
                entity_id="a",
                entity_label="Albania",
                series_id="Sa",
                observations=[(datetime(2024, 1, 1), 89.4)],
            ),
        ],
    )
    defaults.update(kwargs)
    return ChartModel(**defaults)


@pytest.fixture
def inv_root(tmp_path: Path) -> Path:
    return tmp_path / "investigations"


def test_most_recent_investigation_id(inv_root: Path):
    older = Investigation.new("older", title="Older")
    newer = Investigation.new("newer", title="Newer")
    save_investigation(older, root=inv_root)
    save_investigation(newer, root=inv_root)
    path_newer = inv_root / "newer.yaml"
    path_older = inv_root / "older.yaml"
    path_older.touch()
    import time

    time.sleep(0.05)
    path_newer.write_text(path_newer.read_text() + "\n")
    assert most_recent_investigation_id(root=inv_root) == "newer"
    assert resolve_investigation_id(None, root=inv_root) == "newer"
    assert resolve_investigation_id("older", root=inv_root) == "older"


def test_most_recent_raises_when_empty(inv_root: Path):
    inv_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(InvestigationError, match="No investigations"):
        most_recent_investigation_id(root=inv_root)


def test_pick_primary_query_prefers_annotated():
    inv = Investigation.new("demo", title="Demo")
    inv.add_query(metric=["m"], entity=["a", "b"])
    q2 = inv.add_query(
        metric=["m"],
        entity=["a", "b", "c"],
        annotations="Interesting comparison",
    )
    picked = pick_primary_query(inv)
    assert picked is not None
    assert picked.id == q2.id
    assert picked.annotations == "Interesting comparison"


def test_build_preview_model_and_render(inv_root: Path):
    inv = Investigation.new(
        "foreign-banks",
        title="Foreign Bank Assets in Selected Countries",
        notes="Found an interesting comparison between Albania and Bangladesh",
    )
    inv.workspace.metric = [
        "percentage-of-foreign-bank-assets-among-total-bank-assets"
    ]
    inv.workspace.entity = ["albania", "angola"]
    inv.workspace.series = ["DDOI14USA156NWDB"]
    inv.add_query(
        metric=["percentage-of-foreign-bank-assets-among-total-bank-assets"],
        entity=["algeria", "angola"],
    )
    inv.add_query(
        metric=["percentage-of-foreign-bank-assets-among-total-bank-assets"],
        entity=["albania", "angola"],
        annotations=(
            "Interesting that albania and angola have such massive values here."
        ),
    )
    save_investigation(inv, root=inv_root)

    albania = _series("DDOI14ALA156NWDB", "albania", "Albania")
    angola = _series("DDOI14AOA156NWDB", "angola", "Angola")
    us = _series("DDOI14USA156NWDB", "united-states", "United States")

    client = MagicMock()
    client.preview.return_value = []
    client.get_series.side_effect = lambda sid: {
        "DDOI14USA156NWDB": us,
    }[sid]
    client.query.return_value = [
        MagicMock(series=albania),
        MagicMock(series=angola),
    ]
    client.get_metric.return_value = Metric(
        id="percentage-of-foreign-bank-assets-among-total-bank-assets",
        name="Percentage of Foreign Bank Assets Among Total Bank Assets",
        description="",
    )
    client.get_series_observations.side_effect = lambda sid, **kwargs: {
        "DDOI14USA156NWDB": [
            _obs(sid, 2000, 10.0),
            _obs(sid, 2024, 8.9),
        ],
        "DDOI14ALA156NWDB": [
            _obs(sid, 2000, 80.0),
            _obs(sid, 2024, 89.4),
        ],
        "DDOI14AOA156NWDB": [
            _obs(sid, 2000, 50.0),
            _obs(sid, 2024, 82.1),
        ],
    }[sid]

    model = build_preview_model(client, "foreign-banks", root=inv_root)
    assert model.title == "Foreign Bank Assets in Selected Countries"
    assert "Albania" in model.subtitle
    assert model.query_count == 2
    assert len(model.panels) == 2
    assert model.entity_count == 2
    assert model.series_id == "DDOI14USA156NWDB"
    assert "World Bank" in model.source_display
    assert "massive values" in (model.observation or "")
    assert {line.entity_label for line in model.lines} >= {"Albania", "Angola"}

    html = render_html(model)
    assert "Foreign Bank Assets in Selected Countries" in html
    assert "Investigation" in html
    assert "query-list" in html
    assert "query-tab" in html
    assert "data-toggle-panel" in html
    assert "Show queries" not in html
    assert "theme-toggle" in html
    assert "#056704" in html
    assert "Refresh data" in html
    assert "Clone" in html
    assert "Details" in html
    assert "Provenance" in html
    assert "<h3>Summary</h3>" not in html
    assert ">Summary<" not in html
    assert "massive values" in html
    assert "Percentage of Foreign Bank Assets Among Total Bank Assets" in html
    assert "Albania" in html
    assert "bk-root" in html or "bokeh" in html.lower()
    assert 'id="panel-' in html
    assert "Jefferson Street" not in html


def test_render_html_legend_sorted_by_value():
    chart = _chart()
    model = PreviewModel(
        investigation_id="x",
        title="T",
        subtitle=None,
        published=datetime(2026, 11, 12),
        query_count=1,
        panels=[
            QueryPanel(
                query_id=1,
                label="Query 1",
                observation="Note",
                charts=[chart],
            )
        ],
    )
    html = render_html(model)
    assert html.index("89.4%") < html.index("5.1%")
    assert "2024" in html
    assert "Latest values" in html
    assert "legend-grid" in html
    assert "Export PNG" in html
    assert "Jefferson Street" not in html
    assert "chart-source" in html
    assert "World Bank (S1)" in html
    assert "Author observation" in html
    assert "chart-q1-m0" in html
    assert "chart-plot-night" in html
    assert "chart-plot-light" in html
    assert "legend-night" in html
    assert "legend-light" in html
    assert "query-tab" in html
    assert "query-list" in html
    assert "Light mode" in html


def test_aligned_series_leaves_gaps_as_nan():
    from jstdata.preview.render import _aligned_series

    line = SeriesLine(
        entity_id="a",
        entity_label="A",
        series_id="Sa",
        observations=[
            (datetime(2004, 1, 1), 10.0),
            (datetime(2006, 1, 1), 20.0),
        ],
    )
    xs, ys = _aligned_series(line, [2004, 2005, 2006])
    assert [x.year for x in xs] == [2004, 2005, 2006]
    assert ys[0] == 10.0
    assert math.isnan(ys[1])
    assert ys[2] == 20.0


def test_preview_investigation_writes_and_opens(tmp_path: Path, monkeypatch):
    from jstdata.client import JSTDataClient
    from jstdata.preview import preview_investigation

    inv_root = tmp_path / "investigations"
    export_dir = tmp_path / "exports" / "preview"
    inv = Investigation.new("sample", title="Sample Preview")
    inv.workspace.metric = ["m1"]
    inv.workspace.entity = ["e1"]
    inv.add_query(
        metric=["m1"],
        entity=["e1"],
        annotations="An observation note",
    )
    save_investigation(inv, root=inv_root)

    series = _series("S1", "e1", "Entity One", metric_id="m1")
    client = MagicMock(spec=JSTDataClient)
    client.preview.return_value = []
    client.query.return_value = [MagicMock(series=series)]
    client.get_metric.return_value = Metric(id="m1", name="Metric One")
    client.get_series_observations.return_value = [
        _obs("S1", 2010, 1.0),
        _obs("S1", 2020, 2.0),
    ]

    opened: list[str] = []
    monkeypatch.setattr(
        "jstdata.preview.webbrowser.open",
        lambda url: opened.append(url),
    )

    path = preview_investigation(
        client,
        "sample",
        root=inv_root,
        export_dir=export_dir,
        open_browser=True,
    )
    assert path.exists()
    text = path.read_text()
    assert "Sample Preview" in text
    assert "An observation note" in text
    assert "Metric One" in text
    assert "query-list" in text
    assert opened and opened[0].startswith("file:")


def test_cli_help_lists_preview():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "preview" in result.output


def test_progress_spinner_updates_without_tty(capsys):
    from jstdata.preview.progress import ProgressSpinner

    with ProgressSpinner(enabled=False) as progress:
        progress.update("Resolving investigation…")
        progress.update("Fetching observations 1/2…")
        progress.succeed("Wrote preview.html")
    out = capsys.readouterr().err
    assert "Resolving investigation…" in out
    assert "Fetching observations 1/2…" in out
    assert "Wrote preview.html" in out
    assert "✓" in out


def test_preview_investigation_reports_progress(tmp_path: Path, monkeypatch):
    from jstdata.client import JSTDataClient
    from jstdata.preview import preview_investigation

    inv_root = tmp_path / "investigations"
    export_dir = tmp_path / "exports" / "preview"
    inv = Investigation.new("progress-demo", title="Progress Demo")
    inv.add_query(metric=["m1"], entity=["e1"])
    save_investigation(inv, root=inv_root)

    series = _series("S1", "e1", "Entity One", metric_id="m1")
    client = MagicMock(spec=JSTDataClient)
    client.preview.return_value = []
    client.query.return_value = [MagicMock(series=series)]
    client.get_metric.return_value = Metric(id="m1", name="Metric One")
    client.get_series_observations.return_value = [_obs("S1", 2010, 1.0)]
    monkeypatch.setattr("jstdata.preview.webbrowser.open", lambda url: None)

    messages: list[str] = []
    path = preview_investigation(
        client,
        "progress-demo",
        root=inv_root,
        export_dir=export_dir,
        open_browser=False,
        progress=messages.append,
    )
    assert path.exists()
    joined = " | ".join(messages)
    assert "Resolving investigation" in joined
    assert "Building query" in joined
    assert "Rendering HTML" in joined
