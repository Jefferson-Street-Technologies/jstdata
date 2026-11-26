"""Render a PreviewModel to a self-contained interactive HTML page.

Charts are SVG (not Bokeh) so they render reliably in hidden tabs and support
client-side remark cite filters (series + date span), matching the TUI.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from .data import (
    ChartModel,
    InvestigationTab,
    PreviewModel,
    QueryPanel,
    RemarkView,
    SeriesLine,
)

# Bright lines for night backgrounds; deeper tones for light backgrounds.
LINE_COLORS_NIGHT = (
    "#3ecf3a",
    "#7CFF6B",
    "#E8D44D",
    "#B388FF",
    "#5B8DEF",
    "#F5F5F5",
    "#FF6B9D",
    "#4ECDC4",
)
LINE_COLORS_LIGHT = (
    "#056704",
    "#1a7f1a",
    "#b8860b",
    "#6b3fa0",
    "#1f5fbf",
    "#333333",
    "#c23b6e",
    "#0f7f78",
)

ChartTheme = str  # "night" | "light"
ChartLike = Union[ChartModel, PreviewModel]


def _theme_palette(theme: ChartTheme) -> tuple[str, ...]:
    return LINE_COLORS_LIGHT if theme == "light" else LINE_COLORS_NIGHT


def _esc(value: Optional[str]) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _format_published(dt: datetime) -> str:
    return f"Published {dt.strftime('%b')} {dt.day}, {dt.year}"


def _format_value(value: float, *, percent: bool) -> str:
    if percent:
        return f"{value:.1f}%"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    return f"{value:.2f}"


def _is_percent_units(chart: ChartLike) -> bool:
    name = (chart.metric_name or "").lower()
    return "percent" in name or "%" in name


def line_color(index: int, theme: ChartTheme = "night") -> str:
    palette = _theme_palette(theme)
    return palette[index % len(palette)]


def _legend_items(
    chart: ChartLike, *, theme: ChartTheme = "night"
) -> list[tuple[str, str, str, str]]:
    """Return ``(color, label, value_text, year)`` sorted by latest value desc."""
    percent = _is_percent_units(chart)
    rows: list[tuple[float, str, str, str, str]] = []
    for i, line in enumerate(chart.lines):
        color = line_color(i, theme)
        latest = line.latest
        if latest is None:
            rows.append((-math.inf, color, line.entity_label, "—", ""))
            continue
        ts, value = latest[0], latest[1]
        rows.append(
            (
                value,
                color,
                line.entity_label,
                _format_value(value, percent=percent),
                str(ts.year),
            )
        )
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(c, lab, val, year) for _, c, lab, val, year in rows]


def _observation_years(lines: list[SeriesLine]) -> list[int]:
    years: set[int] = set()
    for line in lines:
        for ts, *_ in line.observations:
            years.add(ts.year)
    return sorted(years)


def _aligned_series(
    line: SeriesLine, years: list[int]
) -> tuple[list[datetime], list[float]]:
    """Map a series onto the shared year axis; missing years become NaN."""
    by_year = {ts.year: float(val) for ts, val, *_ in line.observations}
    xs: list[datetime] = []
    ys: list[float] = []
    for year in years:
        xs.append(datetime(year, 1, 1))
        ys.append(by_year[year] if year in by_year else float("nan"))
    return xs, ys


def _chart_payload(chart: ChartModel) -> dict[str, Any]:
    """JSON-serializable chart data for client-side SVG rendering."""
    lines_out: list[dict[str, Any]] = []
    for i, line in enumerate(chart.lines):
        points: list[dict[str, Any]] = []
        for ts, val, *rest in line.observations:
            if val is None or math.isnan(float(val)):
                continue
            release_id = (rest[0] if rest else "") or ""
            points.append(
                {
                    "t": ts.strftime("%Y-%m-%d"),
                    "y": float(val),
                    "ms": int(ts.timestamp() * 1000),
                    "release_id": str(release_id).strip(),
                }
            )
        if not points:
            continue
        lines_out.append(
            {
                "id": line.series_id,
                "entity_id": line.entity_id,
                "label": line.entity_label,
                "frequency": line.frequency or "",
                "color_night": line_color(i, "night"),
                "color_light": line_color(i, "light"),
                "points": points,
            }
        )
    return {
        "metric": chart.metric_name,
        "metric_id": chart.metric_id,
        "series_id": chart.series_id,
        "units": chart.units,
        "source": chart.source,
        "percent": _is_percent_units(chart),
        "frequencies": list(chart.frequencies or []),
        "lines": lines_out,
    }


def _primary_release_id(chart: Optional[ChartModel]) -> str:
    """Release id for the chart's display series' latest observation."""
    if chart is None:
        return "—"
    target = chart.series_id
    for line in chart.lines or []:
        if target and line.series_id != target:
            continue
        latest = line.latest
        if latest is None:
            continue
        rid = (latest[2] if len(latest) > 2 else "") or ""
        return rid.strip() or "—"
    for line in chart.lines or []:
        latest = line.latest
        if latest is None:
            continue
        rid = (latest[2] if len(latest) > 2 else "") or ""
        if rid.strip():
            return rid.strip()
    return "—"


def _legend_block(chart: ChartLike, *, theme: ChartTheme) -> str:
    legend = _legend_items(chart, theme=theme)
    legend_year = next((y for *_, y in legend if y), "")
    year_bit = f" · {_esc(legend_year)}" if legend_year else ""
    label_to_sid = {
        ln.entity_label: ln.series_id
        for ln in getattr(chart, "lines", []) or []
    }
    rows = "\n".join(
        f'<div class="legend-row" data-series-id="{_esc(label_to_sid.get(label, ""))}">'
        f'<span class="swatch" style="background:{_esc(color)}"></span>'
        f'<span class="legend-label">{_esc(label)}</span>'
        f'<span class="legend-value">{_esc(value)}</span>'
        f"</div>"
        for color, label, value, _year in legend
    )
    return f"""
        <aside class="legend legend-{theme}" aria-label="Series legend">
          <div class="legend-year">Latest values{year_bit}</div>
          <div class="legend-grid">
            {rows}
          </div>
        </aside>
    """


def _freq_filter_html(frequencies: list[str]) -> str:
    if len(frequencies) <= 1:
        # Still show a read-only chip when we know the frequency.
        if frequencies:
            return (
                f'<span class="freq-chip" title="Series frequency">'
                f"{_esc(frequencies[0])}</span>"
            )
        return ""
    opts = ['<option value="">Any frequency</option>']
    for i, freq in enumerate(frequencies):
        # Default to the first concrete frequency (not "Any").
        selected = " selected" if i == 0 else ""
        opts.append(
            f'<option value="{_esc(freq)}"{selected}>{_esc(freq)}</option>'
        )
    return (
        '<label class="freq-filter-label">Frequency '
        f'<select class="freq-filter">{"".join(opts)}</select></label>'
    )


def _chart_card_html(
    chart: ChartModel,
    *,
    artifact_id: str,
    export_name: str,
    panel_label: Optional[str] = None,
) -> str:
    eyebrow = (
        f'<div class="chart-panel-label">{_esc(panel_label)}</div>'
        if panel_label
        else ""
    )
    payload = _chart_payload(chart)
    payload_json = html.escape(json.dumps(payload, separators=(",", ":")), quote=True)
    freq_ui = _freq_filter_html(list(chart.frequencies or []))
    return f"""
      <section class="chart-card" id="{_esc(artifact_id)}" data-export-name="{export_name}">
        {eyebrow}
        <div class="chart-card-toolbar">
          {freq_ui}
          <button type="button" class="btn export export-chart" title="Download chart as PNG">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M12 3v12"></path>
              <path d="M7 10l5 5 5-5"></path>
              <path d="M5 19h14"></path>
            </svg>
            Export PNG
          </button>
        </div>
        <div class="chart-head">
          <h2>{_esc(chart.metric_name)}</h2>
          <p class="chart-subtitle">{_esc(chart.chart_subtitle)}</p>
          <p class="chart-source">{_esc(chart.source_display)}</p>
        </div>
        <div class="chart-plot-wrap">
          <div class="chart-plot" data-chart="{payload_json}" role="img"
               aria-label="{_esc(chart.metric_name)}"></div>
          <div class="chart-tooltip" hidden></div>
        </div>
        {_legend_block(chart, theme="night")}
        {_legend_block(chart, theme="light")}
        <button type="button" class="table-toggle" aria-expanded="false">
          Show data table
        </button>
        <div class="chart-table-wrap" hidden>
          <table class="chart-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Series</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </section>
    """


def _remarks_html(remarks: list[RemarkView], *, observation: Optional[str]) -> str:
    """Render remarks; cited ones are toggles that filter the panel charts."""
    items = list(remarks or [])
    if not items and observation:
        items = [RemarkView(text=observation)]
    if not items:
        return ""
    blocks: list[str] = []
    for remark in items:
        name = (
            f'<h4 class="remark-name">{_esc(remark.name)}</h4>'
            if remark.name
            else ""
        )
        cite = (
            f'<div class="remark-cite">{_esc(remark.citation)}</div>'
            if remark.citation
            else ""
        )
        if remark.cited:
            series_json = html.escape(
                json.dumps(list(remark.series)), quote=True
            )
            blocks.append(
                f"""
                <button type="button" class="remark remark-toggle"
                        data-series="{series_json}"
                        data-from="{_esc(remark.from_ or '')}"
                        data-to="{_esc(remark.to or '')}"
                        title="Toggle chart filter from this cite">
                  {name}
                  <p>{_esc(remark.text)}</p>
                  {cite}
                  <div class="remark-hint">Click to highlight cite · click again to clear</div>
                </button>
                """
            )
        else:
            blocks.append(
                f"""
                <article class="remark">
                  {name}
                  <p>{_esc(remark.text)}</p>
                  {cite}
                </article>
                """
            )
    return f"""
        <section class="remarks">
          <div class="eyebrow">Remarks</div>
          {"".join(blocks)}
        </section>
        """


def _panel_html(
    panel: QueryPanel,
    *,
    active: bool,
) -> str:
    cards = []
    key = panel.key or str(panel.query_id)
    for i, chart in enumerate(panel.charts):
        artifact_id = f"chart-{key}-m{i}"
        export_name = _esc(f"{key}-{chart.metric_id or i}-chart.png")
        cards.append(
            _chart_card_html(
                chart,
                artifact_id=artifact_id,
                export_name=export_name,
            )
        )
    remarks = _remarks_html(panel.remarks, observation=panel.observation)
    inv_bit = ""
    if panel.investigation_title:
        inv_bit = (
            f'<span class="panel-inv">{_esc(panel.investigation_title)}</span>'
            f'<span class="panel-meta-sep">·</span>'
        )
    header = f"""
      <header class="panel-header">
        <h2 class="panel-title">{_esc(panel.label)}</h2>
        <div class="panel-meta">
          {inv_bit}
          <span class="panel-qid">Query {panel.query_id}</span>
        </div>
      </header>
    """
    hidden = "" if active else " hidden"
    inv = _esc(panel.investigation_id or "")
    return f"""
    <article class="query-panel{hidden}" id="panel-{_esc(key)}"
             data-query-id="{panel.query_id}" data-panel-key="{_esc(key)}"
             data-inv="{inv}" data-panel>
      {header}
      {"".join(cards)}
      {remarks}
    </article>
    """


def _tabs_html(
    panels: list[QueryPanel],
    *,
    active_key: str,
    active_inv: Optional[str] = None,
    filter_inv: bool = False,
) -> str:
    items = []
    for panel in panels:
        key = panel.key or str(panel.query_id)
        active = " is-active" if key == active_key else ""
        metric_hint = ""
        if panel.primary_chart:
            metric_hint = panel.primary_chart.metric_name
        inv = panel.investigation_id or ""
        hidden = ""
        if filter_inv and active_inv and inv and inv != active_inv:
            hidden = " hidden"
        items.append(
            f"""
            <button type="button" class="query-tab{active}"{hidden}
                    data-tab="{_esc(key)}"
                    data-inv="{_esc(inv)}"
                    aria-controls="panel-{_esc(key)}">
              <span class="tab-check" aria-hidden="true">
                <input type="checkbox" class="tab-visibility" data-toggle-panel="{_esc(key)}" checked />
              </span>
              <span class="tab-copy">
                <span class="tab-label">{_esc(panel.label)}</span>
                <span class="tab-meta">{_esc(metric_hint)}</span>
              </span>
            </button>
            """
        )
    return "\n".join(items)


def _inv_tabs_html(tabs: list[InvestigationTab], *, active_id: str) -> str:
    if len(tabs) <= 1:
        return ""
    buttons = []
    for tab in tabs:
        active = " is-active" if tab.id == active_id else ""
        n = len(tab.panel_keys)
        buttons.append(
            f"""
            <button type="button" class="inv-tab{active}" data-inv-tab="{_esc(tab.id)}">
              <span class="inv-tab-label">{_esc(tab.title)}</span>
              <span class="inv-tab-meta">{n} quer{"y" if n == 1 else "ies"}</span>
            </button>
            """
        )
    return f"""
      <nav class="inv-tabs" aria-label="Investigations">
        {"".join(buttons)}
      </nav>
    """


def _entity_labels(chart: Optional[ChartModel]) -> list[str]:
    if chart is None:
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for line in chart.lines or []:
        label = (line.entity_label or line.entity_id or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def _entities_list_html(labels: Sequence[str]) -> str:
    if not labels:
        return "<li>—</li>"
    return "".join(f"<li>{_esc(label)}</li>" for label in labels)


def _details_html(chart: Optional[ChartModel]) -> str:
    if chart is None:
        return "<p class='muted'>No query selected.</p>"
    entities = _entities_list_html(_entity_labels(chart))
    return f"""
        <div class="detail-row">
          <div class="detail-key">Metric</div>
          <div class="detail-val" data-detail="metric">{_esc(chart.metric_name)}</div>
        </div>
        <div class="detail-row">
          <div class="detail-key">Series</div>
          <div class="detail-val" data-detail="series">{_esc(chart.series_id or "—")}</div>
        </div>
        <div class="detail-row">
          <div class="detail-key">Units</div>
          <div class="detail-val" data-detail="units">{_esc(chart.units or "—")}</div>
        </div>
        <div class="detail-row">
          <div class="detail-key">Source</div>
          <div class="detail-val" data-detail="source">{_esc(chart.source)}</div>
        </div>
        <div class="detail-row">
          <div class="detail-key">Release</div>
          <div class="detail-val" data-detail="release">{_esc(_primary_release_id(chart))}</div>
        </div>
        <div class="detail-row detail-row-entities">
          <div class="detail-key">Entities</div>
          <ul class="detail-entities" data-detail="entities">{entities}</ul>
        </div>
    """


_CHART_JS = r"""
(function () {
  function theme() {
    return document.documentElement.getAttribute("data-theme") || "night";
  }

  function parseChart(el) {
    try {
      return JSON.parse(el.getAttribute("data-chart") || "{}");
    } catch (e) {
      return null;
    }
  }

  function fmtVal(v, percent) {
    if (percent) return v.toFixed(1) + "%";
    if (Math.abs(v) >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    return v.toFixed(2);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function cardOf(el) {
    return el.closest(".chart-card");
  }

  function freqOf(el) {
    var card = cardOf(el);
    var sel = card && card.querySelector(".freq-filter");
    return sel ? (sel.value || "") : (el._freq || "");
  }

  function nearestMs(candidates, target) {
    if (!candidates.length) return null;
    var best = candidates[0];
    var bestDist = Math.abs(best - target);
    for (var i = 1; i < candidates.length; i++) {
      var d = Math.abs(candidates[i] - target);
      if (d < bestDist) {
        best = candidates[i];
        bestDist = d;
      }
    }
    return best;
  }

  function markSelectedRows(el) {
    var card = cardOf(el);
    if (!card) return;
    var sel = el._selected;
    card.querySelectorAll(".chart-table tbody tr").forEach(function (tr) {
      var match = !!sel &&
        Number(tr.getAttribute("data-ms")) === sel.ms &&
        tr.getAttribute("data-series-id") === sel.seriesId;
      tr.classList.toggle("is-selected", match);
    });
  }

  function releaseForPoint(line, ms) {
    if (!line || !line.points) return "—";
    for (var i = 0; i < line.points.length; i++) {
      if (line.points[i].ms === ms) {
        return (line.points[i].release_id || "").trim() || "—";
      }
    }
    return "—";
  }

  function applyObservationDetails(el, seriesId, ms) {
    var data = parseChart(el);
    if (!data) return;
    var line = null;
    for (var i = 0; i < (data.lines || []).length; i++) {
      if (data.lines[i].id === seriesId) {
        line = data.lines[i];
        break;
      }
    }
    if (!line) return;
    if (window.jstPreviewDetails && window.jstPreviewDetails.apply) {
      window.jstPreviewDetails.apply({
        metric: data.metric || "",
        series: seriesId || "—",
        units: data.units || "—",
        source: data.source || "—",
        release: releaseForPoint(line, ms),
        entity_labels: [line.label || line.entity_id || seriesId]
      });
    }
  }

  function selectObservation(el, seriesId, ms) {
    if (!el || seriesId == null || ms == null) return;
    document.querySelectorAll(".chart-plot[data-chart]").forEach(function (other) {
      if (other !== el) {
        other._selected = null;
        markSelectedRows(other);
      }
    });
    el._selected = { seriesId: seriesId, ms: ms };
    setHover(el, ms, { fromTable: false });
    markSelectedRows(el);
    applyObservationDetails(el, seriesId, ms);
  }

  function rebuildTable(el, rows, percent) {
    var card = cardOf(el);
    if (!card) return;
    var tbody = card.querySelector(".chart-table tbody");
    if (!tbody) return;
    tbody.innerHTML = rows.map(function (r) {
      return '<tr data-ms="' + r.ms + '" data-series-id="' + esc(r.id) +
        '" data-t="' + esc(r.t) + '"><td>' + esc(r.t) +
        '</td><td><span class="table-swatch" style="background:' + r.color +
        '"></span>' + esc(r.label) + '</td><td>' +
        fmtVal(r.y, percent) + "</td></tr>";
    }).join("");
    tbody.querySelectorAll("tr").forEach(function (tr) {
      tr.addEventListener("mouseenter", function () {
        var ms = Number(tr.getAttribute("data-ms"));
        if (!isNaN(ms)) setHover(el, ms, { fromTable: true });
      });
      tr.addEventListener("mouseleave", function () {
        if (el._hoverFromTable) setHover(el, null, { fromTable: true });
      });
      tr.addEventListener("click", function () {
        var ms = Number(tr.getAttribute("data-ms"));
        var sid = tr.getAttribute("data-series-id") || "";
        if (!isNaN(ms) && sid) selectObservation(el, sid, ms);
      });
    });
    markSelectedRows(el);
  }

  function setHover(el, ms, opts) {
    opts = opts || {};
    el._hoverMs = ms;
    el._hoverFromTable = !!opts.fromTable && ms != null;
    var st = el._chartState;
    var card = cardOf(el);
    var tip = card && card.querySelector(".chart-tooltip");
    var cross = el.querySelector(".chart-crosshair");
    var focus = el.querySelector(".chart-focus-dots");
    if (!st || !cross) {
      if (tip) tip.hidden = true;
      return;
    }
    if (ms == null) {
      cross.setAttribute("visibility", "hidden");
      if (focus) focus.innerHTML = "";
      if (tip) tip.hidden = true;
      if (card) {
        card.querySelectorAll(".chart-table tbody tr").forEach(function (tr) {
          tr.classList.remove("is-hover");
        });
      }
      return;
    }

    var x = st.xScale(ms);
    cross.setAttribute("x1", x.toFixed(1));
    cross.setAttribute("x2", x.toFixed(1));
    cross.setAttribute("visibility", "visible");

    var hits = [];
    st.lines.forEach(function (ln) {
      if (ln.dimmed) return;
      var pt = null;
      for (var i = 0; i < ln.points.length; i++) {
        if (ln.points[i].ms === ms) {
          pt = ln.points[i];
          break;
        }
      }
      if (!pt) return;
      hits.push({ id: ln.id, label: ln.label, color: ln.color, y: pt.y, t: pt.t });
    });

    if (focus) {
      focus.innerHTML = hits.map(function (h) {
        return '<circle cx="' + x.toFixed(1) + '" cy="' +
          st.yScale(h.y).toFixed(1) + '" r="4.5" fill="' + h.color +
          '" stroke="var(--chart-focus-stroke, #fff)" stroke-width="1.5" />';
      }).join("");
    }

    if (tip) {
      if (!hits.length) {
        tip.hidden = true;
      } else {
        tip.hidden = false;
        tip.innerHTML =
          '<div class="tip-date">' + esc(hits[0].t) + "</div>" +
          hits.map(function (h) {
            return '<div class="tip-row"><span class="tip-swatch" style="background:' +
              h.color + '"></span><span class="tip-label">' + esc(h.label) +
              '</span><span class="tip-val">' + fmtVal(h.y, st.percent) +
              "</span></div>";
          }).join("");
        var wrap = card.querySelector(".chart-plot-wrap");
        var rect = (wrap || el).getBoundingClientRect();
        var left = x + 12;
        if (left + 180 > rect.width) left = x - 192;
        tip.style.left = Math.max(8, left) + "px";
        tip.style.top = "16px";
      }
    }

    if (card) {
      card.querySelectorAll(".chart-table tbody tr").forEach(function (tr) {
        var match = Number(tr.getAttribute("data-ms")) === ms;
        tr.classList.toggle("is-hover", match);
      });
      var hoverRow = card.querySelector(".chart-table tbody tr.is-hover");
      if (hoverRow && !opts.fromTable) {
        var wrapTbl = card.querySelector(".chart-table-wrap");
        if (wrapTbl && !wrapTbl.hidden) {
          hoverRow.scrollIntoView({ block: "nearest" });
        }
      }
    }
  }

  function pickSeriesAt(el, ms, clientY) {
    var st = el._chartState;
    if (!st || ms == null) return null;
    var hits = [];
    st.lines.forEach(function (ln) {
      if (ln.dimmed) return;
      for (var i = 0; i < ln.points.length; i++) {
        if (ln.points[i].ms === ms) {
          hits.push({ id: ln.id, y: ln.points[i].y });
          break;
        }
      }
    });
    if (!hits.length) return null;
    if (hits.length === 1) return hits[0].id;
    var rect = el.getBoundingClientRect();
    var localY = ((clientY - rect.top) / Math.max(1, rect.height)) * st.h;
    var best = hits[0];
    var bestDist = Math.abs(st.yScale(best.y) - localY);
    for (var j = 1; j < hits.length; j++) {
      var d = Math.abs(st.yScale(hits[j].y) - localY);
      if (d < bestDist) {
        best = hits[j];
        bestDist = d;
      }
    }
    return best.id;
  }

  function bindPlotPointer(el) {
    if (el._pointerBound) return;
    el._pointerBound = true;
    el.addEventListener("mousemove", function (ev) {
      var st = el._chartState;
      if (!st || !st.msList.length) return;
      var rect = el.getBoundingClientRect();
      var vbW = st.w;
      var localX = ((ev.clientX - rect.left) / Math.max(1, rect.width)) * vbW;
      var msAt = st.minMs + ((localX - st.pad.l) / Math.max(1, st.iw)) * (st.maxMs - st.minMs);
      var snap = nearestMs(st.msList, msAt);
      setHover(el, snap, { fromTable: false });
    });
    el.addEventListener("mouseleave", function () {
      if (!el._hoverFromTable) setHover(el, null);
    });
    el.addEventListener("click", function (ev) {
      var st = el._chartState;
      if (!st || !st.msList.length) return;
      var rect = el.getBoundingClientRect();
      var localX = ((ev.clientX - rect.left) / Math.max(1, rect.width)) * st.w;
      var msAt = st.minMs + ((localX - st.pad.l) / Math.max(1, st.iw)) * (st.maxMs - st.minMs);
      var snap = nearestMs(st.msList, msAt);
      var sid = pickSeriesAt(el, snap, ev.clientY);
      if (sid != null && snap != null) selectObservation(el, sid, snap);
    });
  }

  function drawChart(el, filter) {
    var data = parseChart(el);
    if (!data || !data.lines || !data.lines.length) {
      el.innerHTML = '<div class="chart-empty">No observations</div>';
      el._chartState = null;
      return;
    }
    if (filter === undefined) {
      var panel = el.closest("[data-panel]");
      filter = (panel && panel._citeFilter) || null;
    }
    filter = filter || {};
    var keep = filter.series && filter.series.length
      ? new Set(filter.series)
      : null;
    var from = (filter.from || "").slice(0, 10);
    var to = (filter.to || "").slice(0, 10);
    var freq = freqOf(el);
    var th = theme();

    var lines = data.lines.map(function (ln) {
      if (freq && (ln.frequency || "") && ln.frequency !== freq) {
        return null;
      }
      return {
        id: ln.id,
        label: ln.label,
        frequency: ln.frequency || "",
        color: th === "light" ? ln.color_light : ln.color_night,
        points: ln.points.slice(),
        cited: keep ? keep.has(ln.id) : true,
        dimmed: false
      };
    }).filter(function (ln) { return ln && ln.points.length > 0; });

    if (keep) {
      var anyCited = lines.some(function (ln) { return ln.cited; });
      if (anyCited) {
        lines.forEach(function (ln) { ln.dimmed = !ln.cited; });
      }
    }

    if (!lines.length) {
      el.innerHTML = '<div class="chart-empty">No observations' +
        (freq ? " for " + esc(freq) : "") + "</div>";
      el._chartState = null;
      rebuildTable(el, [], !!data.percent);
      return;
    }

    var w = el.clientWidth || 720;
    var h = 360;
    var pad = { l: 52, r: 16, t: 16, b: 36 };
    var iw = Math.max(40, w - pad.l - pad.r);
    var ih = Math.max(40, h - pad.t - pad.b);

    // Full timeframe — remark cites highlight a band; they do not zoom.
    var allPts = [];
    lines.forEach(function (ln) {
      ln.points.forEach(function (p) { allPts.push(p); });
    });
    var minMs = Math.min.apply(null, allPts.map(function (p) { return p.ms; }));
    var maxMs = Math.max.apply(null, allPts.map(function (p) { return p.ms; }));
    if (minMs === maxMs) maxMs = minMs + 1;
    var ys = allPts.map(function (p) { return p.y; });
    var minY = Math.min.apply(null, ys);
    var maxY = Math.max.apply(null, ys);
    if (data.percent) {
      minY = Math.min(0, minY);
      maxY = Math.max(100, maxY);
    } else if (minY === maxY) {
      minY -= 1;
      maxY += 1;
    } else {
      var padY = (maxY - minY) * 0.08;
      minY -= padY;
      maxY += padY;
    }

    function xScale(ms) {
      return pad.l + ((ms - minMs) / (maxMs - minMs)) * iw;
    }
    function yScale(y) {
      return pad.t + (1 - (y - minY) / (maxY - minY)) * ih;
    }

    var band = "";
    if (from || to) {
      var fromMs = from
        ? Date.parse(from + "T00:00:00Z")
        : minMs;
      var toMs = to
        ? Date.parse(to + "T23:59:59Z")
        : maxMs;
      if (!isNaN(fromMs) && !isNaN(toMs) && toMs >= fromMs) {
        var x0 = Math.max(pad.l, xScale(Math.max(fromMs, minMs)));
        var x1 = Math.min(pad.l + iw, xScale(Math.min(toMs, maxMs)));
        if (x1 > x0) {
          band = '<rect class="chart-cite-band" x="' + x0.toFixed(1) +
            '" y="' + pad.t + '" width="' + (x1 - x0).toFixed(1) +
            '" height="' + ih + '" />';
        }
      }
    }

    var grid = "";
    var ticks = 4;
    for (var i = 0; i <= ticks; i++) {
      var yy = minY + (maxY - minY) * (i / ticks);
      var y = yScale(yy);
      grid += '<line x1="' + pad.l + '" x2="' + (pad.l + iw) + '" y1="' + y +
        '" y2="' + y + '" class="chart-grid" />';
      grid += '<text x="' + (pad.l - 8) + '" y="' + (y + 4) +
        '" text-anchor="end" class="chart-axis">' +
        fmtVal(yy, !!data.percent) + "</text>";
    }
    var yearStart = new Date(minMs).getUTCFullYear();
    var yearEnd = new Date(maxMs).getUTCFullYear();
    var yearStep = Math.max(1, Math.ceil((yearEnd - yearStart) / 6));
    for (var year = yearStart; year <= yearEnd; year += yearStep) {
      var ms = Date.UTC(year, 0, 1);
      if (ms < minMs || ms > maxMs) continue;
      var x = xScale(ms);
      grid += '<text x="' + x + '" y="' + (h - 10) +
        '" text-anchor="middle" class="chart-axis">' + year + "</text>";
    }

    var paths = lines.map(function (ln) {
      var op = ln.dimmed ? "0.22" : "1";
      var d = ln.points.map(function (p, idx) {
        return (idx ? "L" : "M") + xScale(p.ms).toFixed(1) + "," +
          yScale(p.y).toFixed(1);
      }).join(" ");
      var dots = ln.points.map(function (p) {
        return '<circle class="chart-dot" data-ms="' + p.ms +
          '" data-series-id="' + esc(ln.id) + '" cx="' +
          xScale(p.ms).toFixed(1) + '" cy="' + yScale(p.y).toFixed(1) +
          '" r="2.5" fill="' + ln.color + '" opacity="' + op + '" />';
      }).join("");
      return '<g opacity="' + op + '"><path d="' + d +
        '" fill="none" stroke="' + ln.color +
        '" stroke-width="2.2" />' + dots + "</g>";
    }).join("");

    el.innerHTML =
      '<svg class="chart-svg" viewBox="0 0 ' + w + " " + h +
      '" width="100%" height="' + h + '" preserveAspectRatio="none">' +
      band + grid + paths +
      '<line class="chart-crosshair" x1="0" x2="0" y1="' + pad.t +
      '" y2="' + (pad.t + ih) + '" visibility="hidden" />' +
      '<g class="chart-focus-dots"></g></svg>';

    var msSet = {};
    var tableRows = [];
    lines.forEach(function (ln) {
      if (ln.dimmed) return;
      ln.points.forEach(function (p) {
        msSet[p.ms] = true;
        var inWindow = true;
        if (from && p.t < from) inWindow = false;
        if (to && p.t > to) inWindow = false;
        // When a cite is active, table shows the highlighted window only.
        if ((from || to || keep) && !inWindow) return;
        tableRows.push({
          ms: p.ms,
          t: p.t,
          id: ln.id,
          label: ln.label,
          color: ln.color,
          y: p.y
        });
      });
    });
    tableRows.sort(function (a, b) {
      if (a.ms !== b.ms) return a.ms - b.ms;
      return a.label.localeCompare(b.label);
    });

    el._chartState = {
      pad: pad,
      iw: iw,
      ih: ih,
      w: w,
      h: h,
      minMs: minMs,
      maxMs: maxMs,
      xScale: xScale,
      yScale: yScale,
      lines: lines,
      percent: !!data.percent,
      msList: Object.keys(msSet).map(Number).sort(function (a, b) { return a - b; })
    };

    rebuildTable(el, tableRows, !!data.percent);
    bindPlotPointer(el);
    if (el._selected) {
      setHover(el, el._selected.ms);
      markSelectedRows(el);
    } else if (el._hoverMs != null) {
      setHover(el, el._hoverMs);
    }

    var card = cardOf(el);
    if (card) {
      var focusLines = lines.filter(function (ln) { return !ln.dimmed; });
      var latest = focusLines.map(function (ln) {
        var last = ln.points[ln.points.length - 1];
        return {
          id: ln.id,
          label: ln.label,
          color: ln.color,
          value: fmtVal(last.y, !!data.percent),
          year: String(last.t).slice(0, 4),
          sort: last.y
        };
      }).sort(function (a, b) { return b.sort - a.sort; });
      card.querySelectorAll(".legend").forEach(function (leg) {
        var isNight = leg.classList.contains("legend-night");
        if ((th === "night") !== isNight) return;
        var year = latest.length ? latest[0].year : "";
        var yearEl = leg.querySelector(".legend-year");
        if (yearEl) yearEl.textContent = "Latest values" + (year ? " · " + year : "");
        var gridEl = leg.querySelector(".legend-grid");
        if (!gridEl) return;
        gridEl.innerHTML = latest.map(function (row) {
          return '<div class="legend-row" data-series-id="' + esc(row.id) +
            '"><span class="swatch" style="background:' + row.color +
            '"></span><span class="legend-label">' + esc(row.label) +
            '</span><span class="legend-value">' + row.value + "</span></div>";
        }).join("");
      });
    }
  }

  function redrawAll(root) {
    (root || document).querySelectorAll(".chart-plot[data-chart]").forEach(function (el) {
      var panel = el.closest("[data-panel]");
      var filter = panel && panel._citeFilter ? panel._citeFilter : null;
      drawChart(el, filter);
    });
  }

  function setPanelFilter(panel, filter, btn) {
    panel.querySelectorAll(".remark-toggle").forEach(function (b) {
      b.classList.toggle("is-active", b === btn && !!filter);
    });
    panel._citeFilter = filter;
    panel.querySelectorAll(".chart-plot[data-chart]").forEach(function (el) {
      drawChart(el, filter);
    });
  }

  window.jstPreviewCharts = {
    redrawAll: redrawAll,
    drawChart: drawChart,
    setPanelFilter: setPanelFilter,
    setHover: setHover,
    selectObservation: selectObservation,
    theme: theme
  };

  document.addEventListener("DOMContentLoaded", function () {
    redrawAll(document);
    window.addEventListener("resize", function () {
      redrawAll(document);
    });
  });
})();
"""


def render_html(model: PreviewModel) -> str:
    """Return a full HTML document for the investigation preview."""
    if not any(p.charts for p in model.panels):
        raise ValueError("PreviewModel has no charts to render.")

    active = model.panels[0]
    active_key = active.key or str(active.query_id)
    active_inv = (
        model.tabs[0].id
        if model.tabs
        else (active.investigation_id or model.investigation_id)
    )
    multi_inv = len(model.tabs) > 1
    panels_html = "\n".join(
        _panel_html(
            panel,
            active=(
                not multi_inv
                or panel.investigation_id == active_inv
                or not panel.investigation_id
            ),
        )
        for panel in model.panels
    )
    tabs_html = _tabs_html(
        model.panels,
        active_key=active_key,
        active_inv=active_inv,
        filter_inv=multi_inv,
    )
    inv_tabs_html = _inv_tabs_html(model.tabs, active_id=active_inv)
    details_html = _details_html(active.primary_chart)

    entity_count = active.entity_count
    entity_noun = "country" if entity_count == 1 else "countries"
    subtitle_html = (
        f'<p class="subtitle">{_esc(model.subtitle)}</p>'
        if model.subtitle
        else ""
    )
    scope_label = "Project" if model.is_project else "Investigation"

    details_payload = {
        (p.key or str(p.query_id)): {
            "metric": (p.primary_chart.metric_name if p.primary_chart else ""),
            "series": (p.primary_chart.series_id or "—") if p.primary_chart else "—",
            "units": (p.primary_chart.units or "—") if p.primary_chart else "—",
            "source": (p.primary_chart.source if p.primary_chart else "—"),
            "release": _primary_release_id(p.primary_chart),
            "entity_labels": _entity_labels(p.primary_chart),
            "entities": p.entity_count,
            "source_display": (
                p.primary_chart.source_display if p.primary_chart else "—"
            ),
            "inv": p.investigation_id or "",
        }
        for p in model.panels
    }
    details_json = html.escape(json.dumps(details_payload), quote=True)
    tabs_json = html.escape(
        json.dumps(
            {
                t.id: {"title": t.title, "keys": list(t.panel_keys)}
                for t in model.tabs
            }
        ),
        quote=True,
    )

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="night">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(model.title)}</title>
  <style>
    :root {{
      --bg: #0a0a0a;
      --panel: #121212;
      --text: #e8e8e8;
      --muted: #8a8a8a;
      --border: #2a2a2a;
      --divider: #222;
      --accent: #3ecf3a;
      --chart-export-bg: #0a0a0a;
    }}
    html[data-theme="light"] {{
      --bg: #f6f4ef;
      --panel: #ffffff;
      --text: #1a1a1a;
      --muted: #6b6b6b;
      --border: #e0dcd3;
      --divider: #ebe7df;
      --accent: #056704;
      --chart-export-bg: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px 40px 64px;
      background: var(--bg);
      color: var(--text);
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    .page-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 40px;
      max-width: 1280px;
      margin: 0 auto;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 40px;
      font-weight: 600;
      letter-spacing: -0.02em;
    }}
    .subtitle {{
      margin: 0 0 16px;
      color: var(--muted);
      font-size: 15px;
      max-width: 52rem;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 24px;
    }}
    .meta .dot {{
      width: 3px;
      height: 3px;
      border-radius: 50%;
      background: var(--muted);
    }}
    .inv-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 4px 0 22px;
    }}
    .inv-tab {{
      appearance: none;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      border-radius: 999px;
      padding: 8px 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: baseline;
      gap: 8px;
      font: inherit;
    }}
    .inv-tab.is-active {{
      color: var(--text);
      border-color: var(--accent);
      background: var(--panel);
    }}
    .inv-tab-label {{ font-weight: 600; font-size: 13px; }}
    .inv-tab-meta {{ font-size: 11px; opacity: 0.75; }}
    .query-panel {{
      margin: 0 0 40px;
      padding: 0 0 36px;
      border-bottom: 1px solid var(--divider);
    }}
    .query-panel:last-child {{
      margin-bottom: 0;
      padding-bottom: 0;
      border-bottom: none;
    }}
    .query-panel[hidden],
    .query-tab[hidden] {{
      display: none !important;
    }}
    .panel-header {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      margin: 0 0 18px;
      padding: 0 0 14px;
      border-bottom: 1px solid var(--border);
    }}
    .panel-title {{
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      letter-spacing: -0.01em;
    }}
    .panel-meta {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .panel-meta-sep {{
      opacity: 0.6;
    }}
    .panel-qid {{
      font-variant-numeric: tabular-nums;
    }}
    .chart-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px 22px 18px;
      margin-bottom: 20px;
      overflow: hidden;
    }}
    .chart-card-toolbar {{
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }}
    .freq-filter-label {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: var(--muted);
    }}
    .freq-filter {{
      appearance: none;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 5px 10px;
      font: inherit;
      font-size: 12px;
      cursor: pointer;
    }}
    .freq-chip {{
      font-size: 11px;
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3px 10px;
    }}
    .chart-panel-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .chart-head h2 {{
      margin: 0;
      font-size: 20px;
      font-weight: 600;
    }}
    .chart-subtitle, .chart-source {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .chart-source {{ font-size: 12px; margin-bottom: 14px; }}
    .chart-plot-wrap {{
      position: relative;
      width: 100%;
    }}
    .chart-plot {{
      width: 100%;
      min-height: 360px;
      border-radius: 8px;
      background: #0a0a0a;
      cursor: crosshair;
    }}
    html[data-theme="light"] .chart-plot {{
      background: #ffffff;
    }}
    .chart-svg {{ display: block; }}
    .chart-grid {{ stroke: #222; stroke-width: 1; }}
    html[data-theme="light"] .chart-grid {{ stroke: #e8e4dc; }}
    .chart-axis {{
      fill: #888;
      font-size: 11px;
      font-family: inherit;
    }}
    html[data-theme="light"] .chart-axis {{ fill: #6b6b6b; }}
    .chart-cite-band {{
      fill: color-mix(in srgb, var(--accent) 18%, transparent);
      stroke: none;
    }}
    .chart-crosshair {{
      stroke: var(--accent);
      stroke-width: 1.25;
      stroke-dasharray: 4 3;
      pointer-events: none;
    }}
    .chart-tooltip {{
      position: absolute;
      z-index: 3;
      min-width: 140px;
      max-width: 240px;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      backdrop-filter: blur(6px);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
      pointer-events: none;
      font-size: 12px;
    }}
    .tip-date {{
      color: var(--muted);
      margin-bottom: 8px;
      font-size: 11px;
    }}
    .tip-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 4px;
    }}
    .tip-swatch {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex: 0 0 auto;
    }}
    .tip-label {{ flex: 1 1 auto; min-width: 0; }}
    .tip-val {{ color: var(--muted); }}
    .chart-empty {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 360px;
      color: var(--muted);
      font-size: 14px;
    }}
    .table-toggle {{
      appearance: none;
      margin-top: 14px;
      padding: 0;
      border: none;
      background: transparent;
      color: var(--accent);
      font: inherit;
      font-size: 12px;
      cursor: pointer;
      text-align: left;
    }}
    .table-toggle:hover {{ text-decoration: underline; }}
    .chart-table-wrap {{
      margin-top: 10px;
      max-height: 280px;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    .chart-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    .chart-table th,
    .chart-table td {{
      padding: 8px 12px;
      text-align: left;
      border-bottom: 1px solid var(--divider);
      white-space: nowrap;
    }}
    .chart-table th {{
      position: sticky;
      top: 0;
      background: var(--panel);
      color: var(--muted);
      font-weight: 500;
      z-index: 1;
    }}
    .chart-table tbody tr {{
      cursor: pointer;
    }}
    .chart-table tbody tr:hover,
    .chart-table tbody tr.is-hover {{
      background: color-mix(in srgb, var(--accent) 14%, transparent);
    }}
    .chart-table tbody tr.is-selected {{
      background: color-mix(in srgb, var(--accent) 22%, transparent);
      box-shadow: inset 3px 0 0 var(--accent);
    }}
    .table-swatch {{
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 8px;
      vertical-align: middle;
    }}
    .legend {{
      margin-top: 8px;
      padding-top: 16px;
      border-top: 1px solid var(--border);
    }}
    html[data-theme="night"] .legend-light,
    html[data-theme="light"] .legend-night {{
      display: none !important;
    }}
    .legend-year {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 12px;
    }}
    .legend-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 8px 20px;
    }}
    .legend-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
    }}
    .swatch {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex: 0 0 auto;
    }}
    .legend-label {{ flex: 1 1 auto; min-width: 0; }}
    .legend-value {{ color: var(--muted); }}
    .remarks {{
      margin-top: 8px;
      border: 1px solid var(--border);
      border-left: 3px solid var(--accent);
      border-radius: 10px;
      padding: 18px 20px;
      background: var(--panel);
    }}
    .remarks .eyebrow {{ margin-bottom: 12px; }}
    .remark, .remark-toggle {{
      display: block;
      width: 100%;
      text-align: left;
      margin: 0 0 16px;
      padding: 0;
      border: none;
      background: transparent;
      color: inherit;
      font: inherit;
    }}
    .remark:last-child, .remark-toggle:last-child {{ margin-bottom: 0; }}
    .remark-toggle {{
      cursor: pointer;
      border-radius: 8px;
      padding: 10px 12px;
      margin-left: -12px;
      margin-right: -12px;
      width: calc(100% + 24px);
    }}
    .remark-toggle:hover {{
      background: color-mix(in srgb, var(--accent) 10%, transparent);
    }}
    .remark-toggle.is-active {{
      background: color-mix(in srgb, var(--accent) 16%, transparent);
      box-shadow: inset 0 0 0 1px var(--accent);
    }}
    .remark-name {{
      margin: 0 0 6px;
      font-size: 14px;
      font-weight: 600;
    }}
    .remark p, .remark-toggle p {{
      margin: 0;
      font-size: 14px;
      line-height: 1.55;
    }}
    .remark-cite {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    .remark-hint {{
      margin-top: 8px;
      color: var(--accent);
      font-size: 11px;
      opacity: 0.85;
    }}
    .sidebar {{
      display: flex;
      flex-direction: column;
      gap: 28px;
      position: sticky;
      top: 24px;
      align-self: start;
    }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .btn {{
      appearance: none;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text);
      border-radius: 8px;
      padding: 8px 12px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
    .btn svg {{ width: 16px; height: 16px; }}
    .btn.export {{ font-size: 12px; padding: 6px 10px; }}
    .details h3, .query-list h3 {{
      margin: 0 0 12px;
      font-size: 15px;
      font-weight: 600;
    }}
    .detail-row {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 0;
      border-top: 1px solid var(--divider);
      font-size: 13px;
    }}
    .detail-row-entities {{
      align-items: flex-start;
    }}
    .detail-key {{ font-weight: 500; }}
    .detail-val {{ color: var(--muted); text-align: right; }}
    .detail-entities {{
      margin: 0;
      padding: 0;
      list-style: none;
      color: var(--muted);
      text-align: right;
    }}
    .detail-entities li + li {{
      margin-top: 4px;
    }}
    .query-list {{
      border-top: 1px solid var(--divider);
      padding-top: 18px;
    }}
    .query-tab {{
      width: 100%;
      appearance: none;
      background: transparent;
      border: 1px solid transparent;
      border-radius: 10px;
      color: var(--text);
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 10px 8px;
      cursor: pointer;
      font: inherit;
      text-align: left;
    }}
    .query-tab.is-active {{
      border-color: var(--border);
      background: var(--panel);
    }}
    .tab-check {{ margin-top: 3px; }}
    .tab-label {{ display: block; font-size: 13px; font-weight: 600; }}
    .tab-meta {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-top: 2px;
    }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    @media (max-width: 960px) {{
      body {{ padding: 24px; }}
      .page-shell {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      h1 {{ font-size: 32px; }}
    }}
  </style>
</head>
<body data-details="{details_json}" data-inv-tabs="{tabs_json}" data-active-inv="{_esc(active_inv)}">
  <div class="page-shell">
    <main>
      <div class="eyebrow">{scope_label}</div>
      <h1>{_esc(model.title)}</h1>
      {subtitle_html}
      <div class="meta">
        <span>{_esc(_format_published(model.published))}</span>
        <span class="dot"></span>
        <span>{model.query_count} query{"s" if model.query_count != 1 else ""}</span>
        <span class="dot"></span>
        <span id="meta-entities">{entity_count} {entity_noun}</span>
      </div>
      {inv_tabs_html}
      <div id="panels">
        {panels_html}
      </div>
    </main>

    <aside class="sidebar">
      <div class="actions">
        <button type="button" class="btn theme" id="theme-toggle" title="Toggle light / night mode">
          Light mode
        </button>
        <button type="button" class="btn refresh" disabled title="Re-run jst preview to refresh">
          Refresh data
        </button>
        <button type="button" class="btn" disabled title="Coming soon">Clone</button>
      </div>
      <section class="details">
        <h3>Details</h3>
        {details_html}
      </section>
      <section class="query-list" aria-label="Queries">
        <h3>Queries</h3>
        {tabs_html}
      </section>
    </aside>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
  <script>
{_CHART_JS}
  </script>
  <script>
    (function () {{
      var details = {{}};
      var invTabs = {{}};
      try {{ details = JSON.parse(document.body.getAttribute("data-details") || "{{}}"); }} catch (e) {{}}
      try {{ invTabs = JSON.parse(document.body.getAttribute("data-inv-tabs") || "{{}}"); }} catch (e) {{}}
      var activeInv = document.body.getAttribute("data-active-inv") || "";

      var root = document.documentElement;
      var themeBtn = document.getElementById("theme-toggle");
      function applyTheme(theme) {{
        root.setAttribute("data-theme", theme);
        try {{ localStorage.setItem("jst-preview-theme", theme); }} catch (e) {{}}
        if (themeBtn) {{
          themeBtn.textContent = theme === "night" ? "Light mode" : "Night mode";
        }}
        if (window.jstPreviewCharts) window.jstPreviewCharts.redrawAll(document);
      }}
      var saved = null;
      try {{ saved = localStorage.getItem("jst-preview-theme"); }} catch (e) {{}}
      applyTheme(saved === "light" || saved === "night" ? saved : "night");
      if (themeBtn) {{
        themeBtn.addEventListener("click", function () {{
          applyTheme(root.getAttribute("data-theme") === "night" ? "light" : "night");
        }});
      }}

      function escText(s) {{
        return String(s == null ? "" : s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      }}

      function applyDetails(info) {{
        if (!info) return;
        ["metric", "series", "units", "source", "release"].forEach(function (key) {{
          var el = document.querySelector('[data-detail="' + key + '"]');
          if (el) el.textContent = info[key] || "—";
        }});
        var list = document.querySelector('[data-detail="entities"]');
        if (list) {{
          var labels = info.entity_labels || [];
          if (!labels.length) {{
            list.innerHTML = "<li>—</li>";
          }} else {{
            list.innerHTML = labels.map(function (label) {{
              return "<li>" + escText(label) + "</li>";
            }}).join("");
          }}
        }}
      }}

      window.jstPreviewDetails = {{ apply: applyDetails }};

      function setActiveTab(id) {{
        document.querySelectorAll(".query-tab").forEach(function (tab) {{
          tab.classList.toggle("is-active", tab.getAttribute("data-tab") === String(id));
        }});
        var info = details[String(id)];
        if (!info) return;
        applyDetails(info);
        var ent = document.getElementById("meta-entities");
        if (ent) {{
          var n = info.entities || 0;
          ent.textContent = n + (n === 1 ? " country" : " countries");
        }}
      }}

      function setActiveInv(invId) {{
        activeInv = invId || "";
        document.body.setAttribute("data-active-inv", activeInv);
        document.querySelectorAll(".inv-tab").forEach(function (tab) {{
          tab.classList.toggle("is-active", tab.getAttribute("data-inv-tab") === activeInv);
        }});
        var keys = (invTabs[activeInv] && invTabs[activeInv].keys) || null;
        var firstVisible = null;
        document.querySelectorAll(".query-tab").forEach(function (tab) {{
          var inv = tab.getAttribute("data-inv") || "";
          var show = !activeInv || !inv || inv === activeInv;
          tab.hidden = !show;
          if (show && !firstVisible) firstVisible = tab.getAttribute("data-tab");
        }});
        document.querySelectorAll("[data-panel]").forEach(function (panel) {{
          var inv = panel.getAttribute("data-inv") || "";
          var key = panel.getAttribute("data-panel-key") || "";
          var inTab = !keys || keys.indexOf(key) >= 0;
          var show = (!activeInv || !inv || inv === activeInv) && inTab;
          var box = document.querySelector('.tab-visibility[data-toggle-panel="' + key + '"]');
          if (box && !box.checked) show = false;
          panel.hidden = !show;
        }});
        if (firstVisible) setActiveTab(firstVisible);
        if (window.jstPreviewCharts) {{
          requestAnimationFrame(function () {{
            window.jstPreviewCharts.redrawAll(document);
          }});
        }}
      }}

      document.querySelectorAll(".inv-tab").forEach(function (tab) {{
        tab.addEventListener("click", function () {{
          setActiveInv(tab.getAttribute("data-inv-tab") || "");
        }});
      }});

      document.querySelectorAll(".query-tab").forEach(function (tab) {{
        tab.addEventListener("click", function (ev) {{
          if (ev.target && ev.target.classList && ev.target.classList.contains("tab-visibility")) return;
          var id = tab.getAttribute("data-tab");
          var panel = document.getElementById("panel-" + id);
          if (!panel) return;
          var box = tab.querySelector(".tab-visibility");
          if (box && !box.checked) {{
            box.checked = true;
            panel.hidden = false;
          }}
          setActiveTab(id);
          panel.scrollIntoView({{ behavior: "smooth", block: "start" }});
          if (window.jstPreviewCharts) {{
            requestAnimationFrame(function () {{
              window.jstPreviewCharts.redrawAll(panel);
            }});
          }}
        }});
      }});

      document.querySelectorAll(".tab-visibility").forEach(function (box) {{
        box.addEventListener("click", function (ev) {{ ev.stopPropagation(); }});
        box.addEventListener("change", function () {{
          var id = box.getAttribute("data-toggle-panel");
          var panel = document.getElementById("panel-" + id);
          if (!panel) return;
          panel.hidden = !box.checked;
          if (box.checked) {{
            setActiveTab(id);
            if (window.jstPreviewCharts) {{
              requestAnimationFrame(function () {{
                window.jstPreviewCharts.redrawAll(panel);
              }});
            }}
          }}
        }});
      }});

      document.querySelectorAll(".remark-toggle").forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var panel = btn.closest("[data-panel]");
          if (!panel || !window.jstPreviewCharts) return;
          var already = btn.classList.contains("is-active");
          if (already) {{
            window.jstPreviewCharts.setPanelFilter(panel, null, null);
            return;
          }}
          var series = [];
          try {{ series = JSON.parse(btn.getAttribute("data-series") || "[]"); }} catch (e) {{}}
          var filter = {{
            series: series,
            from: btn.getAttribute("data-from") || "",
            to: btn.getAttribute("data-to") || ""
          }};
          window.jstPreviewCharts.setPanelFilter(panel, filter, btn);
        }});
      }});

      document.querySelectorAll(".freq-filter").forEach(function (sel) {{
        sel.addEventListener("change", function () {{
          var card = sel.closest(".chart-card");
          var plot = card && card.querySelector(".chart-plot[data-chart]");
          if (!plot || !window.jstPreviewCharts) return;
          window.jstPreviewCharts.drawChart(plot);
        }});
      }});

      document.querySelectorAll(".table-toggle").forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var card = btn.closest(".chart-card");
          var wrap = card && card.querySelector(".chart-table-wrap");
          if (!wrap) return;
          var open = wrap.hidden;
          wrap.hidden = !open;
          btn.setAttribute("aria-expanded", open ? "true" : "false");
          btn.textContent = open ? "Hide data table" : "Show data table";
        }});
      }});

      document.querySelectorAll(".export-chart").forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var target = btn.closest(".chart-card");
          if (!target || typeof html2canvas !== "function") return;
          btn.disabled = true;
          var prev = btn.innerHTML;
          btn.textContent = "Exporting…";
          var bg = getComputedStyle(document.documentElement)
            .getPropertyValue("--chart-export-bg").trim() || "#0a0a0a";
          html2canvas(target, {{ backgroundColor: bg, scale: 2, useCORS: true }})
            .then(function (canvas) {{
              var a = document.createElement("a");
              a.download = target.getAttribute("data-export-name") || "chart.png";
              a.href = canvas.toDataURL("image/png");
              a.click();
              btn.disabled = false;
              btn.innerHTML = prev;
            }})
            .catch(function () {{
              btn.disabled = false;
              btn.innerHTML = prev;
            }});
        }});
      }});

      if (Object.keys(invTabs).length > 1) {{
        setActiveInv(activeInv || Object.keys(invTabs)[0]);
      }}
    }})();
  </script>
</body>
</html>
"""


def write_preview_html(model: PreviewModel, path: Path) -> Path:
    """Write the preview HTML to ``path`` and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(model), encoding="utf-8")
    return path
