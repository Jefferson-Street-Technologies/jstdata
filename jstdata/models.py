from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class Frequency(str, Enum):
    ANNUAL = "Annual"
    QUARTERLY = "Quarterly"
    MONTHLY = "Monthly"
    DAILY = "Daily"
    INTRADAY = "Intraday"


class RelationshipType(str, Enum):
    EQUIVALENT_TO = "equivalent_to"
    CLASSIFIED_AS = "classified_as"
    PART_OF = "part_of"
    HAS_SECURITY = "has_security"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return datetime.min


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise ValueError(f"Cannot parse date from {value!r}")


@dataclass(frozen=True)
class Entity:
    id: str
    label: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entity":
        return cls(id=data["id"], label=data["label"])


@dataclass(frozen=True)
class MetricVariant:
    frequency: Optional[str]
    source: str
    seasonal_adjustment: Optional[str]
    units: str
    series_count: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetricVariant":
        return cls(
            frequency=data.get("frequency"),
            source=data.get("source", ""),
            seasonal_adjustment=data.get("seasonal_adjustment"),
            units=data.get("units", ""),
            series_count=int(data.get("series_count") or 0),
        )


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    description: str = ""
    variants: List[MetricVariant] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Metric":
        variants_raw = data.get("variants") or []
        variants = [
            MetricVariant.from_dict(v)
            for v in variants_raw
            if isinstance(v, dict)
        ]
        return cls(
            id=data["id"],
            name=data.get("name", data.get("label", "")),
            description=data.get("description") or "",
            variants=variants,
        )


@dataclass(frozen=True)
class MetricTaxonomy:
    id: str
    name: str
    relationship: str
    entity_count: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetricTaxonomy":
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            relationship=data.get("relationship", ""),
            entity_count=int(data.get("entity_count") or 0),
        )


@dataclass(frozen=True)
class Taxonomy:
    id: str
    name: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Taxonomy":
        return cls(id=data["id"], name=data.get("name", data.get("label", "")))

@dataclass(frozen=True)
class Resource:
    id: str
    label: str


@dataclass(frozen=True)
class Release:
    id: str
    label: str
    release_date: date
    source: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Release":
        return cls(
            id=data["id"],
            label=data.get("label", ""),
            release_date=_parse_date(data["release_date"]),
            source=data.get("source", ""),
        )


@dataclass(frozen=True)
class EventItem:
    code: str
    title: str
    short_title: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventItem":
        return cls(
            code=data["code"],
            title=data.get("title", ""),
            short_title=data.get("short_title") or data.get("title", ""),
        )


@dataclass(frozen=True)
class Event:
    """One Form 8-K filing by one company (``GET /event``)."""

    accession_number: str
    entity_id: str
    entity_label: str
    tickers: List[str]
    form: str
    is_amendment: bool
    filing_date: date
    accepted_at: datetime
    report_date: Optional[date]
    headline: str
    reported_items: List[EventItem]
    item_codes: List[str]
    url: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        report_date = data.get("report_date")
        return cls(
            accession_number=data["accession_number"],
            entity_id=data["entity_id"],
            entity_label=data.get("entity_label", data["entity_id"]),
            tickers=list(data.get("tickers") or []),
            form=data.get("form", "8-K"),
            is_amendment=bool(data.get("is_amendment", False)),
            filing_date=_parse_date(data["filing_date"]),
            accepted_at=_parse_datetime(data["accepted_at"]),
            report_date=_parse_date(report_date) if report_date else None,
            headline=data.get("headline", ""),
            reported_items=[EventItem.from_dict(i) for i in data.get("reported_items") or []],
            item_codes=list(data.get("item_codes") or []),
            url=data.get("url", ""),
        )


@dataclass(frozen=True)
class PreviewRow:
    metric_id: str
    entity_id: str
    series_count: int
    observation_count: int
    earliest: datetime
    latest: datetime

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PreviewRow":
        return cls(
            metric_id=data["metric_id"],
            entity_id=data["entity_id"],
            series_count=int(data.get("series_count") or 0),
            observation_count=int(data.get("observation_count") or 0),
            earliest=_parse_datetime(data.get("earliest")),
            latest=_parse_datetime(data.get("latest")),
        )

@dataclass(frozen=True)
class Series:
    id: str
    label: str
    frequency: str
    source: str
    units: str
    seasonal_adjustment: str
    last_updated: datetime
    metric_id: str
    entities: List[Entity] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Series":
        # Handle datetime conversion
        last_updated = data.get("last_updated")
        if isinstance(last_updated, str):
            try:
                # API format: 2024-01-01 00:00:00 or ISO
                last_updated = datetime.fromisoformat(last_updated.replace(" ", "T"))
            except ValueError:
                # Fallback for other potential formats
                last_updated = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
        
        if last_updated is None:
            last_updated = datetime.min

        entities = [Entity.from_dict(e) for e in data.get("entities", [])]

        return cls(
            id=data["id"],
            label=data.get("label", ""),
            frequency=data.get("frequency", ""),
            source=data.get("source", ""),
            units=data.get("units", ""),
            # Handle the typo 'seasonal_adjsustment' from API spec while supporting the correct spelling
            seasonal_adjustment=data.get(
                "seasonal_adjustment", data.get("seasonal_adjsustment", "")
            ),
            last_updated=last_updated,
            metric_id=data.get("metric_id", ""),
            entities=entities,
        )


@dataclass(frozen=True)
class EntityRelationship:
    id: str
    relationship: RelationshipType
    taxonomy: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EntityRelationship":
        return cls(
            id=data["id"],
            relationship=RelationshipType(data["relationship"]),
            taxonomy=data.get("taxonomy"),
        )


@dataclass(frozen=True)
class Observation:
    series_id: str
    observation_timestamp: datetime
    release_timestamp: datetime
    value: float
    release_id: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Observation":
        return cls(
            series_id=data["series_id"],
            observation_timestamp=_parse_datetime(data["observation_timestamp"]),
            release_timestamp=_parse_datetime(data["release_timestamp"]),
            value=data["value"],
            release_id=data.get("release_id") or "",
        )

@dataclass(frozen=True)
class TimeSeries:
    series: Series
    observations: List[Observation]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimeSeries":
        series = {
            "id": data.get("id", ""),
            "label": data.get("label", ""),
            "frequency": data.get("frequency", ""),
            "source": data.get("source", ""),
            "units": data.get("units", ""),
            "seasonal_adjustment": data.get("seasonal_adjustment", ""),
            "last_updated": data.get("last_updated", ""),
            "metric_id": data.get("metric_id", ""),
            "entities": data.get("entities", []),
        }
        return cls(
            series=Series.from_dict(series),
            observations=[Observation.from_dict(dict(o, series_id=data["id"])) for o in data["observations"]]
        )
