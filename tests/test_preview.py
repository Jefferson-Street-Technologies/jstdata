"""Tests for ``jst preview`` data resolution and HTML rendering."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from jstdata.cli import cli
from jstdata.investigation.model import (
    Investigation,
    InvestigationError,
    QueryRemark,
)
from jstdata.investigation.store import save_investigation
from jstdata.models import Entity, Metric, Observation, Series
from jstdata.preview.data import (
    ChartModel,
    PreviewModel,
    QueryPanel,
    SeriesLine,
    build_preview_model,
    build_project_preview_model,
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
        units="Percent",
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
    assert "Provenance" not in html
    assert "Show data table" in html
    assert "chart-table" in html
    assert "chart-tooltip" in html
    assert "chart-cite-band" in html
    assert "<h3>Summary</h3>" not in html
    assert ">Summary<" not in html
    assert "massive values" in html
    assert "Percentage of Foreign Bank Assets Among Total Bank Assets" in html
    assert "Albania" in html
    assert "data-chart=" in html
    assert "chart-svg" in html or "jstPreviewCharts" in html
    assert 'id="panel-' in html
    assert "Jefferson Street" not in html


def test_render_html_legend_sorted_by_value():
    chart = _chart(frequencies=["Annual", "Quarterly"])
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
    assert 'data-detail="units"' in html
    assert "Percent" in html
    assert "Remarks" in html
    assert "chart-inv-q1-m0" in html
    assert "data-chart=" in html
    assert "legend-night" in html
    assert "legend-light" in html
    assert "query-tab" in html
    assert "query-list" in html
    assert "Light mode" in html
    assert "jstPreviewCharts" in html
    assert "freq-filter" in html
    assert "Any frequency" in html
    assert "Quarterly" in html
    assert "Show data table" in html
    assert "chart-table-wrap" in html
    assert '"frequency"' in html or "frequencies" in html


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


def _mock_client_for_metric(metric_id: str = "m1", entity_id: str = "e1"):
    series = _series("S1", entity_id, "Entity One", metric_id=metric_id)
    client = MagicMock()
    client.preview.return_value = []
    client.query.return_value = [MagicMock(series=series)]
    client.get_metric.return_value = Metric(id=metric_id, name="Metric One")
    client.get_series.return_value = series
    client.get_series_observations.return_value = [
        _obs("S1", 2010, 1.0),
        _obs("S1", 2020, 2.0),
    ]
    return client


def test_preview_renders_remarks_with_citations(inv_root: Path):
    inv = Investigation.new("remarks-demo", title="Remarks Demo")
    q = inv.add_query(
        metric=["m1"],
        entity=["e1"],
        name="Revenue",
    )
    q.remarks = [
        QueryRemark(text="Broad context for the chart."),
        QueryRemark(
            text="Spike around the IPO window.",
            name="IPO",
            series=["S1"],
            from_="2019-01-01",
            to="2021-12-31",
        ),
    ]
    q.sync_legacy_annotation_fields()
    save_investigation(inv, root=inv_root)

    model = build_preview_model(
        _mock_client_for_metric(), "remarks-demo", root=inv_root
    )
    assert len(model.panels) == 1
    remarks = model.panels[0].remarks
    assert len(remarks) == 2
    assert remarks[0].text.startswith("Broad context")
    assert remarks[1].name == "IPO"
    assert remarks[1].citation is not None
    assert "2019" in remarks[1].citation
    assert "Entity One" in remarks[1].citation or "S1" in remarks[1].citation

    html = render_html(model)
    assert "Remarks" in html
    assert "Broad context" in html
    assert "IPO" in html
    assert "Spike around the IPO" in html
    assert "remark-cite" in html
    assert "remark-toggle" in html
    assert 'data-from="2019-01-01"' in html
    assert 'data-to="2021-12-31"' in html
    assert "data-series=" in html
    assert "Click to highlight cite" in html
    assert "Show data table" in html
    assert "freq-chip" in html or "freq-filter" in html


def test_project_preview_has_investigation_tabs(tmp_path: Path):
    from jstdata.investigation import (
        add_investigation_to_project,
        create_project_with_investigation,
        persist_investigation,
    )
    from jstdata.investigation.project_store import load_project_investigation

    create_project_with_investigation(
        project_id="ftai",
        title="FTAI Aviation",
        investigation_id="fundamentals",
        investigation_title="Fundamentals",
        app_dir=tmp_path,
    )
    add_investigation_to_project(
        "ftai",
        investigation_id="peers",
        title="Peers",
        app_dir=tmp_path,
    )
    fund = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    peers = load_project_investigation("ftai", "peers", app_dir=tmp_path)
    fund.add_query(metric=["m1"], entity=["e1"], name="Fund revenue")
    peers.add_query(metric=["m1"], entity=["e1"], name="Peer revenue")
    persist_investigation(fund, app_dir=tmp_path)
    persist_investigation(peers, app_dir=tmp_path)

    model = build_project_preview_model(
        _mock_client_for_metric(), "ftai", app_dir=tmp_path
    )
    assert model.project_id == "ftai"
    assert model.is_project
    assert [t.id for t in model.tabs] == ["fundamentals", "peers"]
    assert len(model.panels) == 2
    assert {p.key for p in model.panels} == {
        "fundamentals-q1",
        "peers-q1",
    }
    assert model.panels[0].label == "Fund revenue"

    html = render_html(model)
    assert "Project" in html
    assert "inv-tabs" in html
    assert "Fundamentals" in html
    assert "Peers" in html
    assert 'data-inv-tab="fundamentals"' in html
    assert "Fund revenue" in html
    assert "Peer revenue" in html
