"""Render a PreviewModel to a self-contained Bokeh HTML page."""

from __future__ import annotations

import html
import math
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from bokeh.embed import components
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure
from bokeh.resources import CDN

from .data import ChartModel, PreviewModel, QueryPanel, SeriesLine

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


def _theme_chrome(theme: ChartTheme) -> dict[str, str]:
    if theme == "light":
        return {
            "background": "#ffffff",
            "border": "#ffffff",
            "grid": "#e8e4dc",
            "axis": "#d0cbc0",
            "label": "#6b6b6b",
        }
    return {
        "background": "#0a0a0a",
        "border": "#0a0a0a",
        "grid": "#222222",
        "axis": "#333333",
        "label": "#888888",
    }


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
    """Return (color, label, value_text, year) sorted by latest value desc."""
    percent = _is_percent_units(chart)
    rows: list[tuple[float, str, str, str, str]] = []
    for i, line in enumerate(chart.lines):
        color = line_color(i, theme)
        latest = line.latest
        if latest is None:
            rows.append((-math.inf, color, line.entity_label, "—", ""))
            continue
        ts, value = latest
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
    year = next((r[4] for r in rows if r[4]), "")
    return [(c, lab, val, year) for _, c, lab, val, _ in rows]


def _observation_years(lines: list[SeriesLine]) -> list[int]:
    years: set[int] = set()
    for line in lines:
        for ts, _ in line.observations:
            years.add(ts.year)
    return sorted(years)


def _aligned_series(
    line: SeriesLine, years: list[int]
) -> tuple[list[datetime], list[float]]:
    """Map a series onto the shared year axis; missing years become NaN."""
    by_year = {ts.year: float(val) for ts, val in line.observations}
    xs: list[datetime] = []
    ys: list[float] = []
    for year in years:
        xs.append(datetime(year, 1, 1))
        ys.append(by_year[year] if year in by_year else float("nan"))
    return xs, ys


def build_chart(chart: ChartLike, *, theme: ChartTheme = "night"):
    """Create a multi-line Bokeh figure themed for night or light mode."""
    percent = _is_percent_units(chart)
    years = _observation_years(chart.lines)
    chrome = _theme_chrome(theme)

    fig = figure(
        height=360,
        sizing_mode="stretch_width",
        x_axis_type="datetime",
        toolbar_location=None,
        background_fill_color=chrome["background"],
        border_fill_color=chrome["border"],
        outline_line_color=None,
    )
    fig.min_border_left = 16
    fig.min_border_right = 12
    fig.min_border_top = 8
    fig.min_border_bottom = 8
    fig.grid.grid_line_color = chrome["grid"]
    fig.axis.axis_line_color = chrome["axis"]
    fig.axis.major_tick_line_color = chrome["axis"]
    fig.axis.minor_tick_line_color = None
    fig.axis.major_label_text_color = chrome["label"]
    fig.axis.axis_label_text_color = chrome["label"]
    fig.yaxis.axis_label = None
    fig.xaxis.axis_label = None
    if percent:
        fig.y_range.start = 0
        fig.y_range.end = 100

    hover = HoverTool(
        tooltips=[
            ("Entity", "@entity"),
            ("Year", "@x{%Y}"),
            ("Value", "@y{0.1f}"),
        ],
        formatters={"@x": "datetime"},
        mode="mouse",
    )
    fig.add_tools(hover)

    for i, line in enumerate(chart.lines):
        if not line.observations or not years:
            continue
        xs, ys = _aligned_series(line, years)
        color = line_color(i, theme)
        source = ColumnDataSource(
            data={
                "x": xs,
                "y": ys,
                "entity": [line.entity_label] * len(xs),
            }
        )
        fig.line("x", "y", source=source, line_width=2.2, line_color=color)
        finite_xs = [x for x, y in zip(xs, ys) if not math.isnan(y)]
        finite_ys = [y for y in ys if not math.isnan(y)]
        if finite_xs:
            dots = ColumnDataSource(
                data={
                    "x": finite_xs,
                    "y": finite_ys,
                    "entity": [line.entity_label] * len(finite_xs),
                }
            )
            fig.scatter(
                "x",
                "y",
                source=dots,
                size=5,
                fill_color=color,
                line_color=color,
                alpha=0.85,
            )

    return fig


def _legend_block(chart: ChartLike, *, theme: ChartTheme) -> str:
    legend = _legend_items(chart, theme=theme)
    legend_year = next((y for *_, y in legend if y), "")
    year_bit = f" · {_esc(legend_year)}" if legend_year else ""
    rows = "\n".join(
        f'<div class="legend-row">'
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


def _chart_card_html(
    chart: ChartLike,
    *,
    night_div: str,
    light_div: str,
    artifact_id: str,
    export_name: str,
    panel_label: Optional[str] = None,
) -> str:
    eyebrow = (
        f'<div class="chart-panel-label">{_esc(panel_label)}</div>'
        if panel_label
        else ""
    )
    return f"""
      <section class="chart-card" id="{_esc(artifact_id)}" data-export-name="{export_name}">
        {eyebrow}
        <div class="chart-card-toolbar">
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
        <div class="chart-plot chart-plot-night">{night_div}</div>
        <div class="chart-plot chart-plot-light">{light_div}</div>
        {_legend_block(chart, theme="night")}
        {_legend_block(chart, theme="light")}
      </section>
    """


def _panel_html(
    panel: QueryPanel,
    *,
    plot_divs: list[tuple[str, str]],
    active: bool,
) -> str:
    cards = []
    for i, chart in enumerate(panel.charts):
        artifact_id = f"chart-q{panel.query_id}-m{i}"
        export_name = _esc(
            f"query-{panel.query_id}-{chart.metric_id or i}-chart.png"
        )
        label = panel.label if len(panel.charts) == 1 else None
        night_div, light_div = plot_divs[i]
        cards.append(
            _chart_card_html(
                chart,
                night_div=night_div,
                light_div=light_div,
                artifact_id=artifact_id,
                export_name=export_name,
                panel_label=label if i == 0 else None,
            )
        )
    observation = ""
    if panel.observation:
        observation = f"""
        <section class="observation">
          <div class="eyebrow">Author observation</div>
          <p>{_esc(panel.observation)}</p>
        </section>
        """
    multi_label = ""
    if len(panel.charts) > 1:
        multi_label = f'<div class="panel-title">{_esc(panel.label)}</div>'
    hidden = "" if active else " hidden"
    return f"""
    <article class="query-panel{hidden}" id="panel-{panel.query_id}"
             data-query-id="{panel.query_id}" data-panel>
      {multi_label}
      {"".join(cards)}
      {observation}
    </article>
    """


def _tabs_html(panels: list[QueryPanel], *, active_id: int) -> str:
    items = []
    for panel in panels:
        active = " is-active" if panel.query_id == active_id else ""
        metric_hint = ""
        if panel.primary_chart:
            metric_hint = panel.primary_chart.metric_name
        items.append(
            f"""
            <button type="button" class="query-tab{active}"
                    data-tab="{panel.query_id}"
                    aria-controls="panel-{panel.query_id}">
              <span class="tab-check" aria-hidden="true">
                <input type="checkbox" class="tab-visibility" data-toggle-panel="{panel.query_id}" checked />
              </span>
              <span class="tab-copy">
                <span class="tab-label">{_esc(panel.label)}</span>
                <span class="tab-meta">{_esc(metric_hint)}</span>
              </span>
            </button>
            """
        )
    return "\n".join(items)


def _details_html(chart: Optional[ChartModel]) -> str:
    if chart is None:
        return "<p class='muted'>No query selected.</p>"
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
          <div class="detail-key">Source</div>
          <div class="detail-val" data-detail="source">{_esc(chart.source)}</div>
        </div>
        <div class="detail-row">
          <div class="detail-key">Period</div>
          <div class="detail-val" data-detail="period">{_esc(chart.period_label)}</div>
        </div>
    """


def render_html(model: PreviewModel) -> str:
    """Return a full HTML document for the investigation preview."""
    panel_divs: list[list[tuple[str, str]]] = [[] for _ in model.panels]
    scripts: list[str] = []
    for pi, panel in enumerate(model.panels):
        for chart in panel.charts:
            night_fig = build_chart(chart, theme="night")
            light_fig = build_chart(chart, theme="light")
            night_script, night_div = components(night_fig)
            light_script, light_div = components(light_fig)
            scripts.extend([night_script, light_script])
            panel_divs[pi].append((night_div, light_div))

    if not scripts:
        raise ValueError("PreviewModel has no charts to render.")
    script = "\n".join(scripts)

    active = model.panels[0]
    # Show all panels by default so every query is on one page.
    panels_html = "\n".join(
        _panel_html(panel, plot_divs=panel_divs[i], active=True)
        for i, panel in enumerate(model.panels)
    )
    tabs_html = _tabs_html(model.panels, active_id=active.query_id)
    details_html = _details_html(active.primary_chart)

    entity_count = active.entity_count
    entity_noun = "country" if entity_count == 1 else "countries"
    subtitle_html = (
        f'<p class="subtitle">{_esc(model.subtitle)}</p>'
        if model.subtitle
        else ""
    )
    bokeh_css = CDN.render_css()
    bokeh_js = CDN.render_js()

    # Details payloads for JS tab focus updates.
    details_payload = {
        str(p.query_id): {
            "metric": (p.primary_chart.metric_name if p.primary_chart else ""),
            "series": (p.primary_chart.series_id or "—") if p.primary_chart else "—",
            "source": (p.primary_chart.source if p.primary_chart else "—"),
            "period": (p.primary_chart.period_label if p.primary_chart else "—"),
            "entities": p.entity_count,
            "source_display": (
                p.primary_chart.source_display if p.primary_chart else "—"
            ),
        }
        for p in model.panels
    }
    import json

    details_json = html.escape(json.dumps(details_payload), quote=True)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="night">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(model.title)} · Preview</title>
  {bokeh_css}
  <style>
    :root, html[data-theme="night"] {{
      --bg: #050505;
      --panel: #0a0a0a;
      --border: #222;
      --text: #f5f5f5;
      --muted: #8a8a8a;
      --accent: #056704;
      --accent-soft: rgba(5, 103, 4, 0.18);
      --accent-border: rgba(5, 103, 4, 0.55);
      --hover: #111;
      --btn-border: #333;
      --divider: #1a1a1a;
      --dot: #555;
      --chart-export-bg: #0a0a0a;
    }}
    html[data-theme="light"] {{
      --bg: #f4f4f1;
      --panel: #ffffff;
      --border: #ddd9d0;
      --text: #121212;
      --muted: #6b6b6b;
      --accent: #056704;
      --accent-soft: rgba(5, 103, 4, 0.10);
      --accent-border: rgba(5, 103, 4, 0.45);
      --hover: #f0efe8;
      --btn-border: #cfcabe;
      --divider: #ebe7de;
      --dot: #b0aba0;
      --chart-export-bg: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "IBM Plex Sans", "Segoe UI", Helvetica, Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    body {{
      padding: 40px 48px 64px;
      max-width: 1280px;
      margin: 0 auto;
    }}
    .page-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 40px;
      align-items: start;
    }}
    .query-list {{
      border-top: 1px solid var(--divider);
      padding-top: 18px;
    }}
    .query-list h3 {{
      margin: 0 0 12px;
      font-size: 15px;
      font-weight: 600;
    }}
    .query-tab {{
      width: 100%;
      appearance: none;
      background: transparent;
      border: 1px solid transparent;
      border-radius: 8px;
      color: var(--text);
      text-align: left;
      padding: 10px 8px;
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 8px;
      cursor: pointer;
      margin-bottom: 4px;
    }}
    .query-tab:hover {{
      border-color: var(--btn-border);
      background: var(--hover);
    }}
    .query-tab.is-active {{
      border-color: var(--accent-border);
      background: var(--accent-soft);
    }}
    .tab-check {{
      display: flex;
      align-items: flex-start;
      padding-top: 2px;
    }}
    .tab-check input {{
      accent-color: var(--accent);
      cursor: pointer;
    }}
    .tab-copy {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
    }}
    .tab-label {{
      font-size: 13px;
      font-weight: 600;
      line-height: 1.3;
    }}
    .tab-meta {{
      font-size: 11px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      margin-bottom: 12px;
    }}
    h1 {{
      font-size: 40px;
      line-height: 1.1;
      font-weight: 700;
      margin: 0 0 12px;
      letter-spacing: -0.02em;
    }}
    .subtitle {{
      color: var(--muted);
      font-size: 15px;
      margin: 0 0 18px;
      max-width: 52rem;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 10px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 28px;
    }}
    .meta .dot {{
      width: 3px;
      height: 3px;
      border-radius: 50%;
      background: var(--dot);
      display: inline-block;
    }}
    .query-panel {{
      margin-bottom: 36px;
    }}
    .query-panel[hidden] {{
      display: none !important;
    }}
    .panel-title, .chart-panel-label {{
      font-size: 13px;
      color: var(--muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin: 0 0 12px;
      font-weight: 600;
    }}
    .chart-card {{
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px 24px 16px;
      background: var(--panel);
      margin-bottom: 16px;
    }}
    .chart-card-toolbar {{
      display: flex;
      justify-content: flex-end;
      margin: -4px 0 12px;
    }}
    .chart-head h2 {{
      margin: 0;
      font-size: 18px;
      font-weight: 600;
    }}
    .chart-subtitle {{
      margin: 6px 0 4px;
      color: var(--muted);
      font-size: 13px;
    }}
    .chart-source {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 12px;
    }}
    .chart-plot {{
      width: 100%;
      min-height: 360px;
      border-radius: 8px;
    }}
    .chart-plot-night {{ background: #0a0a0a; }}
    .chart-plot-light {{ background: #ffffff; }}
    html[data-theme="night"] .chart-plot-light,
    html[data-theme="night"] .legend-light {{
      display: none !important;
    }}
    html[data-theme="light"] .chart-plot-night,
    html[data-theme="light"] .legend-night {{
      display: none !important;
    }}
    .bk-root {{ background: transparent !important; }}
    .legend {{
      margin-top: 8px;
      padding-top: 16px;
      border-top: 1px solid var(--border);
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
      display: grid;
      grid-template-columns: 10px minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      font-size: 13px;
      min-width: 0;
    }}
    .swatch {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .legend-label {{
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .legend-value {{
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    .observation {{
      margin-top: 8px;
      border: 1px solid var(--border);
      border-left: 3px solid var(--accent);
      border-radius: 10px;
      padding: 18px 20px;
      background: var(--panel);
    }}
    .observation p {{
      margin: 0;
      color: var(--text);
      font-size: 14px;
      line-height: 1.55;
    }}
    .sidebar {{
      display: flex;
      flex-direction: column;
      gap: 28px;
      padding-top: 4px;
      position: sticky;
      top: 24px;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .btn {{
      appearance: none;
      background: transparent;
      color: var(--text);
      border: 1px solid var(--btn-border);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      cursor: default;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    button.btn:not(:disabled) {{ cursor: pointer; }}
    button.btn:not(:disabled):hover {{ border-color: var(--accent-border); }}
    .btn.refresh {{
      color: var(--accent);
      border-color: var(--accent-border);
    }}
    .btn.export, .btn.ghost, .btn.theme {{
      color: var(--text);
      border-color: var(--btn-border);
      font-size: 12px;
      padding: 6px 10px;
    }}
    .btn.icon-only {{ padding: 8px 10px; }}
    .btn svg {{ width: 14px; height: 14px; }}
    .details h3,
    .provenance-label {{
      margin: 0 0 14px;
      font-size: 15px;
      font-weight: 600;
    }}
    .detail-row {{
      display: grid;
      grid-template-columns: 88px 1fr;
      gap: 12px;
      padding: 10px 0;
      border-top: 1px solid var(--divider);
      font-size: 13px;
    }}
    .detail-key {{ color: var(--text); font-weight: 500; }}
    .detail-val {{ color: var(--muted); text-align: right; }}
    .provenance {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 0;
      border-top: 1px solid var(--divider);
      border-bottom: 1px solid var(--divider);
      color: var(--text);
      font-size: 14px;
      font-weight: 500;
    }}
    .provenance .chevron {{
      color: var(--muted);
      font-size: 18px;
      line-height: 1;
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
<body data-details="{details_json}">
  <div class="page-shell">
    <main>
      <div class="eyebrow">Investigation</div>
      <h1>{_esc(model.title)}</h1>
      {subtitle_html}
      <div class="meta">
        <span>{_esc(_format_published(model.published))}</span>
        <span class="dot"></span>
        <span>{model.query_count} query{"s" if model.query_count != 1 else ""}</span>
        <span class="dot"></span>
        <span id="meta-entities">{entity_count} {entity_noun}</span>
      </div>

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
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M21 12a9 9 0 1 1-2.6-6.2"></path>
            <path d="M21 3v6h-6"></path>
          </svg>
          Refresh data
        </button>
        <button type="button" class="btn" disabled title="Coming soon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="8" y="8" width="12" height="12" rx="2"></rect>
            <path d="M4 16V6a2 2 0 0 1 2-2h10"></path>
          </svg>
          Clone
        </button>
        <button type="button" class="btn icon-only" disabled aria-label="Options">···</button>
      </div>

      <section class="details">
        <h3>Details</h3>
        {details_html}
      </section>

      <section class="query-list" aria-label="Queries">
        <h3>Queries</h3>
        {tabs_html}
      </section>

      <div class="provenance">
        <span class="provenance-label">Provenance</span>
        <span class="chevron">›</span>
      </div>
    </aside>
  </div>
  {bokeh_js}
  {script}
  <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
  <script>
    (function () {{
      var details = {{}};
      try {{
        details = JSON.parse(document.body.getAttribute("data-details") || "{{}}");
      }} catch (e) {{}}

      var root = document.documentElement;
      var themeBtn = document.getElementById("theme-toggle");
      function applyTheme(theme) {{
        root.setAttribute("data-theme", theme);
        try {{ localStorage.setItem("jst-preview-theme", theme); }} catch (e) {{}}
        if (themeBtn) {{
          themeBtn.textContent = theme === "night" ? "Light mode" : "Night mode";
        }}
      }}
      var saved = null;
      try {{ saved = localStorage.getItem("jst-preview-theme"); }} catch (e) {{}}
      applyTheme(saved === "light" || saved === "night" ? saved : "night");
      if (themeBtn) {{
        themeBtn.addEventListener("click", function () {{
          applyTheme(root.getAttribute("data-theme") === "night" ? "light" : "night");
        }});
      }}

      function setActiveTab(id) {{
        document.querySelectorAll(".query-tab").forEach(function (tab) {{
          tab.classList.toggle("is-active", tab.getAttribute("data-tab") === String(id));
        }});
        var info = details[String(id)];
        if (!info) return;
        var map = {{
          metric: info.metric,
          series: info.series,
          source: info.source,
          period: info.period
        }};
        Object.keys(map).forEach(function (key) {{
          var el = document.querySelector('[data-detail="' + key + '"]');
          if (el) el.textContent = map[key];
        }});
        document.querySelectorAll(".chart-source").forEach(function (el) {{
          // Leave per-chart sources alone; meta no longer shows a global source.
        }});
        var ent = document.getElementById("meta-entities");
        if (ent) {{
          var n = info.entities || 0;
          ent.textContent = n + (n === 1 ? " country" : " countries");
        }}
      }}

      document.querySelectorAll(".query-tab").forEach(function (tab) {{
        tab.addEventListener("click", function (ev) {{
          if (ev.target && ev.target.classList && ev.target.classList.contains("tab-visibility")) {{
            return;
          }}
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
        }});
      }});

      document.querySelectorAll(".tab-visibility").forEach(function (box) {{
        box.addEventListener("click", function (ev) {{
          ev.stopPropagation();
        }});
        box.addEventListener("change", function () {{
          var id = box.getAttribute("data-toggle-panel");
          var panel = document.getElementById("panel-" + id);
          if (!panel) return;
          panel.hidden = !box.checked;
          if (box.checked) setActiveTab(id);
        }});
      }});

      document.querySelectorAll(".export-chart").forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var target = btn.closest(".chart-card");
          if (!target) return;
          if (typeof html2canvas !== "function") {{
            btn.textContent = "Export unavailable";
            return;
          }}
          btn.disabled = true;
          var prev = btn.innerHTML;
          btn.textContent = "Exporting…";
          var bg = getComputedStyle(document.documentElement)
            .getPropertyValue("--chart-export-bg").trim() || "#0a0a0a";
          html2canvas(target, {{
            backgroundColor: bg,
            scale: 2,
            useCORS: true
          }}).then(function (canvas) {{
            var a = document.createElement("a");
            a.download = target.getAttribute("data-export-name") || "chart.png";
            a.href = canvas.toDataURL("image/png");
            a.click();
            btn.disabled = false;
            btn.innerHTML = prev;
          }}).catch(function () {{
            btn.disabled = false;
            btn.innerHTML = prev;
          }});
        }});
      }});
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
