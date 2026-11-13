"""Release view: /query release_id rows."""

from __future__ import annotations

from datetime import datetime

from jstdata.actions.release_view import rows_from_timeseries
from jstdata.models import Entity, Observation, Series, TimeSeries


def _ts(
    *,
    metric_id: str,
    label: str,
    value: float,
    units: str = "USD",
) -> TimeSeries:
    return TimeSeries(
        series=Series(
            id=f"series-{metric_id}",
            label=label,
            frequency="Quarterly",
            source="sec",
            units=units,
            seasonal_adjustment="",
            last_updated=datetime(2024, 1, 1),
            metric_id=metric_id,
            entities=[Entity(id="cik:1", label="Acme")],
        ),
        observations=[
            Observation(
                series_id=f"series-{metric_id}",
                observation_timestamp=datetime(2024, 1, 1),
                release_timestamp=datetime(2024, 2, 1),
                value=value,
                release_id="accn-1",
            )
        ],
    )


def test_rows_from_timeseries_sorted_by_metric_label():
    rows = rows_from_timeseries(
        [
            _ts(metric_id="cash", label="Cash", value=10.0),
            _ts(metric_id="long-term-debt", label="Long-term debt", value=99.5),
        ]
    )
    assert [r.metric_id for r in rows] == ["cash", "long-term-debt"]
    assert rows[0].value == 10.0
    assert rows[0].units == "USD"
    assert rows[1].metric_label == "Long-term debt"


def test_rows_prefer_series_label_over_metric_cache():
    """Shared metric ids must not reuse another entity's cached label."""
    rows = rows_from_timeseries(
        [
            _ts(
                metric_id="accounts-payable-current",
                label="Accounts Payable, Current of Tronox Holdings plc",
                value=437_000_000.0,
            )
        ],
        labels={
            "accounts-payable-current": (
                "Accounts Payable, Current of 10x Genomics, Inc."
            )
        },
    )
    assert rows[0].metric_label == (
        "Accounts Payable, Current of Tronox Holdings plc"
    )


def test_rows_from_timeseries_empty_observations():
    ts = _ts(metric_id="cash", label="Cash", value=1.0)
    empty = TimeSeries(series=ts.series, observations=[])
    rows = rows_from_timeseries([empty])
    assert len(rows) == 1
    assert rows[0].value is None
