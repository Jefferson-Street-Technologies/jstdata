"""Resolve an investigation into chart-ready preview data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from ..investigation.model import (
    Investigation,
    InvestigationError,
    Query,
    QueryRemark,
)
from ..investigation.query_compose import frequency_options_for
from ..investigation.store import (
    investigation_path,
    investigations_dir,
    list_investigations,
    load_investigation,
)
from ..models import Observation, Series

if TYPE_CHECKING:
    from ..client import JSTDataClient

ProgressFn = Callable[[str], None]


def _emit(progress: Optional[ProgressFn], message: str) -> None:
    if progress is not None:
        progress(message)

HISTORY_START = "2000-01-01"
OBS_PAGE = 500


def _panel_key(investigation_id: str, query_id: int) -> str:
    """Stable unique id for HTML/JS (safe across multi-tab projects)."""
    inv = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in investigation_id)
    return f"{inv}-q{int(query_id)}"


@dataclass
class SeriesLine:
    """One entity line on the preview chart."""

    entity_id: str
    entity_label: str
    series_id: str
    # (observation_timestamp, value, release_id)
    observations: list[tuple[datetime, float, str]] = field(default_factory=list)
    frequency: Optional[str] = None

    @property
    def latest(self) -> Optional[tuple[datetime, float, str]]:
        if not self.observations:
            return None
        return self.observations[-1]


@dataclass
class ChartModel:
    """One metric chart within a query panel."""

    metric_id: Optional[str]
    metric_name: str
    series_id: Optional[str]
    source: str
    source_display: str
    entity_count: int
    units: Optional[str] = None
    lines: list[SeriesLine] = field(default_factory=list)
    frequencies: list[str] = field(default_factory=list)

    @property
    def period_label(self) -> str:
        years: list[int] = []
        for line in self.lines:
            for ts, *_ in line.observations:
                years.append(ts.year)
        if not years:
            return "—"
        lo, hi = min(years), max(years)
        if lo == hi:
            return str(lo)
        return f"{lo} – {hi}"

    @property
    def chart_subtitle(self) -> str:
        n = self.entity_count
        noun = "country" if n == 1 else "countries"
        period = self.period_label
        if period == "—":
            return f"Selected {noun}"
        return f"Selected {noun}, {period}"


@dataclass
class RemarkView:
    """One research remark ready for the static preview page."""

    text: str
    name: Optional[str] = None
    series: list[str] = field(default_factory=list)
    series_labels: list[str] = field(default_factory=list)
    from_: Optional[str] = None
    to: Optional[str] = None

    @property
    def cited(self) -> bool:
        return bool(self.series or self.from_ or self.to)

    @property
    def citation(self) -> Optional[str]:
        """Human citation line, e.g. ``Albania · 2020–2024``."""
        who = ", ".join(self.series_labels or self.series)
        span = ""
        if self.from_ or self.to:

            def _year(raw: Optional[str]) -> str:
                text = (raw or "").strip()
                return text[:4] if len(text) >= 4 and text[:4].isdigit() else text or "…"

            span = f"{_year(self.from_)}–{_year(self.to)}"
        parts = [p for p in (who, span) if p]
        return " · ".join(parts) if parts else None


@dataclass
class InvestigationTab:
    """One investigation in a multi-tab project preview."""

    id: str
    title: str
    panel_keys: list[str] = field(default_factory=list)


@dataclass
class QueryPanel:
    """One saved query as a preview tab/panel."""

    query_id: int
    label: str
    observation: Optional[str]
    charts: list[ChartModel] = field(default_factory=list)
    key: str = ""
    investigation_id: str = ""
    investigation_title: str = ""
    remarks: list[RemarkView] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.key:
            inv = self.investigation_id or "inv"
            self.key = _panel_key(inv, self.query_id)

    @property
    def entity_count(self) -> int:
        if not self.charts:
            return 0
        return max(c.entity_count for c in self.charts)

    @property
    def primary_chart(self) -> Optional[ChartModel]:
        return self.charts[0] if self.charts else None


@dataclass
class PreviewModel:
    """Everything needed to render the static investigation preview page."""

    investigation_id: str
    title: str
    subtitle: Optional[str]
    published: datetime
    query_count: int
    panels: list[QueryPanel] = field(default_factory=list)
    # Multi-investigation local preview (remote publish awaits Workstream C).
    project_id: Optional[str] = None
    tabs: list[InvestigationTab] = field(default_factory=list)
    # Backward-compatible alias populated from ``tabs``.
    tab_labels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.tabs and not self.tab_labels:
            self.tab_labels = [t.title for t in self.tabs]

    @property
    def is_project(self) -> bool:
        return bool(self.project_id) or len(self.tabs) > 1

    @property
    def active_panel(self) -> Optional[QueryPanel]:
        return self.panels[0] if self.panels else None

    @property
    def entity_count(self) -> int:
        panel = self.active_panel
        return panel.entity_count if panel else 0

    @property
    def metric_name(self) -> str:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.metric_name if chart else "Metric"

    @property
    def series_id(self) -> Optional[str]:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.series_id if chart else None

    @property
    def source(self) -> str:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.source if chart else "—"

    @property
    def source_display(self) -> str:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.source_display if chart else "—"

    @property
    def observation(self) -> Optional[str]:
        for panel in self.panels:
            if panel.observation:
                return panel.observation
            for remark in panel.remarks:
                if remark.text:
                    return remark.text
        return None

    @property
    def lines(self) -> list[SeriesLine]:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.lines if chart else []

    @property
    def period_label(self) -> str:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.period_label if chart else "—"

    @property
    def chart_subtitle(self) -> str:
        chart = self.active_panel.primary_chart if self.active_panel else None
        return chart.chart_subtitle if chart else ""


def most_recent_investigation_id(*, root: Optional[Path] = None) -> str:
    """Return the investigation id with the newest YAML mtime."""
    if root is None:
        from ..investigation.project_store import (
            list_all_investigations,
            project_investigations_dir,
        )

        pairs = list_all_investigations()
        if pairs:
            ranked = sorted(
                pairs,
                key=lambda pair: (
                    project_investigations_dir(pair[0]) / f"{pair[1]}.yaml"
                ).stat().st_mtime,
                reverse=True,
            )
            return ranked[0][1]

    ids = list_investigations(root=root)
    if not ids:
        raise InvestigationError("No investigations found.")
    ranked = sorted(
        ids,
        key=lambda inv_id: investigation_path(inv_id, root=root).stat().st_mtime,
        reverse=True,
    )
    return ranked[0]


def resolve_investigation_id(
    investigation_id: Optional[str], *, root: Optional[Path] = None
) -> str:
    if investigation_id:
        return investigation_id
    return most_recent_investigation_id(root=root)


def pick_primary_query(inv: Investigation) -> Optional[Query]:
    """Prefer an annotated metric×entity query; else any metric×entity query."""
    candidates = list_previewable_queries(inv)
    if not candidates:
        return None
    annotated = [q for q in candidates if q.annotations]
    pool = annotated or candidates
    return sorted(pool, key=lambda q: q.id)[0]


def list_previewable_queries(inv: Investigation) -> list[Query]:
    """Report-membership queries that can produce charts.

    Prefer metric×entity cartesian queries; also include series-only queries
    (explicit series ids) so report charts that cite series stay previewable.
    """
    return sorted(
        (
            q
            for q in inv.queries.values()
            if q.in_report
            and ((q.metric and q.entity) or q.series)
        ),
        key=lambda q: q.id,
    )


def _series_label_map(charts: Sequence[ChartModel]) -> dict[str, str]:
    out: dict[str, str] = {}
    for chart in charts:
        for line in chart.lines:
            if line.series_id and line.entity_label:
                out[line.series_id] = line.entity_label
    return out


def _remark_views(
    remarks: Sequence[QueryRemark],
    *,
    series_labels: Optional[dict[str, str]] = None,
) -> list[RemarkView]:
    labels = series_labels or {}
    views: list[RemarkView] = []
    for remark in remarks:
        if not (remark.text or "").strip():
            continue
        sids = list(remark.series or [])
        views.append(
            RemarkView(
                text=remark.text.strip(),
                name=(remark.name or None),
                series=sids,
                series_labels=[labels.get(sid, sid) for sid in sids],
                from_=remark.from_,
                to=remark.to,
            )
        )
    return views


def _observation_from_remarks(remarks: Sequence[RemarkView]) -> Optional[str]:
    """Legacy single-blob observation: first freeform remark text."""
    for remark in remarks:
        if not remark.cited and remark.text:
            return remark.text
    for remark in remarks:
        if remark.text:
            return remark.text
    return None


def _query_label(query: Query) -> str:
    if query.name:
        return query.name
    if query.metric:
        # Short slug → title-ish label for the tab.
        slug = query.metric[0].replace("-", " ")
        label = slug[:1].upper() + slug[1:]
        if len(query.metric) > 1:
            label = f"{label} +{len(query.metric) - 1}"
        return f"Query {query.id} · {label}"
    return f"Query {query.id}"


def _source_display(source: str, series_id: Optional[str]) -> str:
    """Human source chip for the meta row (World Bank styling when GFDD)."""
    src = (source or "").strip()
    sid = series_id or ""
    lower = src.lower()
    if "global financial development" in lower or "gfdd" in lower:
        label = "World Bank"
    elif src:
        label = src
    else:
        label = "Source"
    if sid:
        return f"{label} ({sid})"
    return label


def _details_source(source: str) -> str:
    lower = (source or "").lower()
    if "global financial development" in lower or "gfdd" in lower:
        return "World Bank, Global Financial Development Database"
    return source or "—"


def _fetch_observations(
    client: JSTDataClient,
    series_id: str,
    *,
    start_date: str = HISTORY_START,
    cache: Optional[dict[str, list[Observation]]] = None,
) -> list[Observation]:
    """Page through series observations from ``start_date``."""
    if cache is not None and series_id in cache:
        return cache[series_id]
    out: list[Observation] = []
    offset = 0
    while True:
        page = client.get_series_observations(
            series_id,
            start_date=start_date,
            limit=OBS_PAGE,
            offset=offset,
        )
        if not page:
            break
        out.extend(page)
        if len(page) < OBS_PAGE:
            break
        offset += len(page)
    out.sort(key=lambda o: o.observation_timestamp)
    if cache is not None:
        cache[series_id] = out
    return out


def _resolve_series_records(
    client: JSTDataClient,
    *,
    metric: Sequence[str],
    entity: Sequence[str],
    series: Sequence[str],
) -> list[Series]:
    """Resolve one Series record per entity (prefer explicit series ids)."""
    if series and not entity:
        return [client.get_series(sid) for sid in series]

    if metric and entity:
        client.preview(
            metric=list(metric),
            entity=list(entity),
            limit=max(50, len(metric) * len(entity)),
        )

    if series:
        records = [client.get_series(sid) for sid in series]
        covered = {e.id for s in records for e in s.entities}
        missing = [eid for eid in entity if eid not in covered]
        if not missing:
            return records
        extra = client.query(
            metric=list(metric) if metric else None,
            entity=missing,
            tail=1,
            limit=max(50, len(missing) * max(1, len(metric) or 1)),
        )
        return records + [ts.series for ts in extra]

    if not metric or not entity:
        return []

    results = client.query(
        metric=list(metric),
        entity=list(entity),
        tail=1,
        limit=max(50, len(metric) * len(entity)),
    )
    return [ts.series for ts in results]


def _build_chart_model(
    client: JSTDataClient,
    *,
    metric_id: str,
    entities: Sequence[str],
    series_ids: Sequence[str],
    obs_cache: dict[str, list[Observation]],
    metric_name_cache: dict[str, str],
    progress: Optional[ProgressFn] = None,
) -> Optional[ChartModel]:
    _emit(progress, f"Resolving series for {metric_id}…")
    series_records = _resolve_series_records(
        client,
        metric=[metric_id],
        entity=list(entities),
        series=list(series_ids),
    )
    # Keep series that belong to this metric (cartesian queries may return more).
    series_records = [
        s for s in series_records if not s.metric_id or s.metric_id == metric_id
    ]
    if not series_records:
        return None

    if metric_id not in metric_name_cache:
        try:
            metric_name_cache[metric_id] = (
                client.get_metric(metric_id).name or metric_id
            )
        except Exception:
            metric_name_cache[metric_id] = metric_id
    metric_name = metric_name_cache[metric_id]

    lines: list[SeriesLine] = []
    total = len(series_records)
    for idx, series in enumerate(series_records, start=1):
        ent = series.entities[0] if series.entities else None
        entity_id = ent.id if ent else series.id
        entity_label = ent.label if ent else series.label or series.id
        if series.id not in obs_cache:
            _emit(
                progress,
                f"Fetching observations {idx}/{total}: {entity_label}…",
            )
        obs = _fetch_observations(client, series.id, cache=obs_cache)
        points = [
            (
                o.observation_timestamp,
                float(o.value),
                (o.release_id or "").strip(),
            )
            for o in obs
            if o.value is not None
        ]
        freq = (series.frequency or "").strip() or None
        lines.append(
            SeriesLine(
                entity_id=entity_id,
                entity_label=entity_label,
                series_id=series.id,
                observations=points,
                frequency=freq,
            )
        )

    display_series = series_ids[0] if series_ids else lines[0].series_id
    display_record = next(
        (s for s in series_records if s.id == display_series),
        series_records[0],
    )
    source = display_record.source
    units = (display_record.units or "").strip() or None
    available = {
        (ln.frequency or "").strip()
        for ln in lines
        if (ln.frequency or "").strip()
    }
    freqs = [f for f in frequency_options_for(available) if f]
    return ChartModel(
        metric_id=metric_id,
        metric_name=metric_name,
        series_id=display_series,
        source=_details_source(source),
        source_display=_source_display(source, display_series),
        entity_count=len(entities) if entities else len(lines),
        units=units,
        lines=lines,
        frequencies=freqs,
    )


def _build_series_only_charts(
    client: JSTDataClient,
    *,
    series_ids: Sequence[str],
    obs_cache: dict[str, list[Observation]],
    metric_name_cache: dict[str, str],
    progress: Optional[ProgressFn] = None,
) -> list[ChartModel]:
    """One chart per metric among explicit series ids."""
    records = _resolve_series_records(
        client, metric=[], entity=[], series=list(series_ids)
    )
    if not records:
        return []
    by_metric: dict[str, list[Series]] = {}
    for series in records:
        mid = series.metric_id or series.id
        by_metric.setdefault(mid, []).append(series)
    charts: list[ChartModel] = []
    for metric_id, group in by_metric.items():
        chart = _build_chart_model(
            client,
            metric_id=metric_id,
            entities=[],
            series_ids=[s.id for s in group],
            obs_cache=obs_cache,
            metric_name_cache=metric_name_cache,
            progress=progress,
        )
        if chart is not None:
            charts.append(chart)
    return charts


def _build_query_panel(
    client: JSTDataClient,
    query: Query,
    *,
    fallback_series: Sequence[str],
    obs_cache: dict[str, list[Observation]],
    metric_name_cache: dict[str, str],
    investigation_id: str,
    investigation_title: str,
    progress: Optional[ProgressFn] = None,
) -> Optional[QueryPanel]:
    series_ids = list(query.series) or list(fallback_series)
    charts: list[ChartModel] = []
    if query.metric:
        for metric_id in query.metric:
            chart = _build_chart_model(
                client,
                metric_id=metric_id,
                entities=query.entity,
                series_ids=series_ids,
                obs_cache=obs_cache,
                metric_name_cache=metric_name_cache,
                progress=progress,
            )
            if chart is not None:
                charts.append(chart)
    elif series_ids:
        charts.extend(
            _build_series_only_charts(
                client,
                series_ids=series_ids,
                obs_cache=obs_cache,
                metric_name_cache=metric_name_cache,
                progress=progress,
            )
        )
    if not charts:
        return None
    remarks = _remark_views(
        query.remarks or [],
        series_labels=_series_label_map(charts),
    )
    observation = _observation_from_remarks(remarks) or query.annotations
    return QueryPanel(
        query_id=query.id,
        label=_query_label(query),
        observation=observation,
        charts=charts,
        key=_panel_key(investigation_id, query.id),
        investigation_id=investigation_id,
        investigation_title=investigation_title,
        remarks=remarks,
    )


def _workspace_panel(
    client: JSTDataClient,
    inv: Investigation,
    *,
    obs_cache: dict[str, list[Observation]],
    metric_name_cache: dict[str, str],
    progress: Optional[ProgressFn] = None,
) -> Optional[QueryPanel]:
    ws = inv.workspace
    if not (ws.metric and ws.entity):
        return None
    charts: list[ChartModel] = []
    for metric_id in ws.metric:
        chart = _build_chart_model(
            client,
            metric_id=metric_id,
            entities=ws.entity,
            series_ids=ws.series,
            obs_cache=obs_cache,
            metric_name_cache=metric_name_cache,
            progress=progress,
        )
        if chart is not None:
            charts.append(chart)
    if not charts:
        return None
    return QueryPanel(
        query_id=0,
        label="Workspace",
        observation=inv.annotations,
        charts=charts,
        key=_panel_key(inv.id, 0),
        investigation_id=inv.id,
        investigation_title=inv.title or inv.id,
        remarks=[],
    )


def build_preview_model(
    client: JSTDataClient,
    investigation_id: Optional[str] = None,
    *,
    root: Optional[Path] = None,
    progress: Optional[ProgressFn] = None,
) -> PreviewModel:
    """Load investigation, fetch history for each query, return a render model."""
    _emit(progress, "Resolving investigation…")
    inv_id = resolve_investigation_id(investigation_id, root=root)
    if root is None:
        from ..investigation.project_store import (
            find_investigation_project,
            load_project_investigation,
            project_investigations_dir,
        )

        pid = find_investigation_project(inv_id)
        if pid is not None:
            inv = load_project_investigation(pid, inv_id)
            path = project_investigations_dir(pid) / f"{inv_id}.yaml"
        else:
            inv = load_investigation(inv_id, root=root)
            path = investigation_path(inv_id, root=root)
    else:
        inv = load_investigation(inv_id, root=root)
        path = investigation_path(inv_id, root=root)
    published = datetime.fromtimestamp(path.stat().st_mtime)
    _emit(progress, f"Loaded {inv.id!r} ({inv.title})")

    obs_cache: dict[str, list[Observation]] = {}
    metric_name_cache: dict[str, str] = {}
    panels: list[QueryPanel] = []

    queries = list_previewable_queries(inv)
    total = len(queries)
    for i, query in enumerate(queries, start=1):
        label = _query_label(query)
        _emit(progress, f"Building query {i}/{total}: {label}…")
        panel = _build_query_panel(
            client,
            query,
            fallback_series=inv.workspace.series,
            obs_cache=obs_cache,
            metric_name_cache=metric_name_cache,
            investigation_id=inv.id,
            investigation_title=inv.title or inv.id,
            progress=progress,
        )
        if panel is not None:
            panels.append(panel)

    if not panels:
        _emit(progress, "Falling back to workspace basket…")
        workspace = _workspace_panel(
            client,
            inv,
            obs_cache=obs_cache,
            metric_name_cache=metric_name_cache,
            progress=progress,
        )
        if workspace is not None:
            panels.append(workspace)

    if not panels:
        raise InvestigationError(
            f"Investigation {inv_id!r} has nothing to preview "
            "(need metric×entity queries, series queries, or a workspace basket)."
        )

    tab = InvestigationTab(
        id=inv.id,
        title=inv.title or inv.id,
        panel_keys=[p.key for p in panels],
    )
    return PreviewModel(
        investigation_id=inv.id,
        title=inv.title,
        subtitle=inv.notes,
        published=published,
        query_count=len([q for q in inv.queries.values() if q.in_report]),
        panels=panels,
        tabs=[tab],
        tab_labels=[tab.title],
    )


def build_project_preview_model(
    client: JSTDataClient,
    project_id: str,
    *,
    app_dir: Optional[Path] = None,
    progress: Optional[ProgressFn] = None,
) -> PreviewModel:
    """Local multi-investigation preview (remote multi-tab awaits Workstream C)."""
    from ..investigation.project_store import (
        load_project,
        load_project_investigation,
        project_dir,
    )

    project = load_project(project_id, app_dir=app_dir)
    _emit(progress, f"Loaded project {project.id!r} ({project.title})")
    obs_cache: dict[str, list[Observation]] = {}
    metric_name_cache: dict[str, str] = {}
    panels: list[QueryPanel] = []
    tabs: list[InvestigationTab] = []
    total_queries = 0

    for inv_id in project.investigations:
        inv = load_project_investigation(
            project_id, inv_id, app_dir=app_dir
        )
        inv_title = inv.title or inv_id
        queries = list_previewable_queries(inv)
        total_queries += len(
            [q for q in inv.queries.values() if q.in_report]
        )
        tab_keys: list[str] = []
        for query in queries:
            _emit(
                progress,
                f"Building {inv_title} · {_query_label(query)}…",
            )
            panel = _build_query_panel(
                client,
                query,
                fallback_series=inv.workspace.series,
                obs_cache=obs_cache,
                metric_name_cache=metric_name_cache,
                investigation_id=inv.id,
                investigation_title=inv_title,
                progress=progress,
            )
            if panel is not None:
                # Query label stays short; investigation chrome is the tab bar.
                panels.append(panel)
                tab_keys.append(panel.key)
        if tab_keys:
            tabs.append(
                InvestigationTab(
                    id=inv.id, title=inv_title, panel_keys=tab_keys
                )
            )

    if not panels:
        raise InvestigationError(
            f"Project {project_id!r} has no report queries to preview."
        )

    published = datetime.fromtimestamp(
        project_dir(project_id, app_dir=app_dir).stat().st_mtime
    )
    subtitle = project.notes
    if not subtitle and len(tabs) > 1:
        subtitle = "Local multi-tab preview"
    return PreviewModel(
        investigation_id=project.id,
        title=project.title,
        subtitle=subtitle,
        published=published,
        query_count=total_queries,
        panels=panels,
        project_id=project.id,
        tabs=tabs,
        tab_labels=[t.title for t in tabs],
    )
