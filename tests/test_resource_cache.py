"""Tests for the persistent resource label cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jstdata.models import Resource
from jstdata.resource_cache import CACHE_TTL, ResourceLabelCache, cache_key
from jstdata.investigation.model import Workspace
from jstdata.actions.base import hydrate_labels


def test_cache_key():
    assert cache_key("entity", "france") == "entity:france"


def test_hydrate_refreshes_workspace_when_any_stale(tmp_path: Path):
    cache = ResourceLabelCache(path=tmp_path / "resources.json")
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    stale = now - CACHE_TTL - timedelta(minutes=1)
    cache.put_many(
        [("entity", "france", "France"), ("metric", "gdp", "GDP")],
        pulled_at=stale,
    )

    client = MagicMock()
    client.get_resources.return_value = [
        Resource(id="france", label="France"),
        Resource(id="germany", label="Germany"),
        Resource(id="gdp", label="Gross Domestic Product"),
    ]
    workspace = Workspace(entity=["france", "germany"], metric=["gdp"])
    labels: dict[str, str] = {}

    cache.hydrate_workspace(client, workspace, labels, now=now)

    client.get_resources.assert_called_once()
    called_ids = client.get_resources.call_args[0][0]
    assert set(called_ids) == {"france", "germany", "gdp"}
    assert labels == {
        "france": "France",
        "germany": "Germany",
        "gdp": "Gross Domestic Product",
    }
    # Fresh entries written for the whole workspace
    assert cache.get("entity", "germany")["label"] == "Germany"
    assert cache.get("metric", "gdp")["label"] == "Gross Domestic Product"
    pulled = datetime.fromisoformat(cache.get("entity", "france")["pulled_at"])
    assert pulled == now


def test_hydrate_uses_cache_when_all_fresh(tmp_path: Path):
    cache = ResourceLabelCache(path=tmp_path / "resources.json")
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    cache.put_many(
        [("entity", "france", "France"), ("metric", "gdp", "GDP")],
        pulled_at=now - timedelta(hours=1),
    )
    client = MagicMock()
    workspace = Workspace(entity=["france"], metric=["gdp"])
    labels: dict[str, str] = {}

    cache.hydrate_workspace(client, workspace, labels, now=now)

    client.get_resources.assert_not_called()
    assert labels == {"france": "France", "gdp": "GDP"}


def test_hydrate_labels_wrapper_uses_injected_cache(tmp_path: Path):
    cache = ResourceLabelCache(path=tmp_path / "resources.json")
    now = datetime.now(timezone.utc)
    cache.put_many([("series", "s1", "Series One")], pulled_at=now)
    client = MagicMock()
    workspace = Workspace(series=["s1"])
    labels: dict[str, str] = {}

    hydrate_labels(client, workspace, labels, cache=cache)
    client.get_resources.assert_not_called()
    assert labels["s1"] == "Series One"


def test_missing_cache_entry_triggers_full_refresh(tmp_path: Path):
    cache = ResourceLabelCache(path=tmp_path / "resources.json")
    now = datetime.now(timezone.utc)
    cache.put_many([("entity", "france", "France")], pulled_at=now)

    client = MagicMock()
    client.get_resources.return_value = [
        Resource(id="france", label="France"),
        Resource(id="germany", label="Germany"),
    ]
    workspace = Workspace(entity=["france", "germany"])
    labels: dict[str, str] = {}
    cache.hydrate_workspace(client, workspace, labels, now=now)

    client.get_resources.assert_called_once()
    assert set(client.get_resources.call_args[0][0]) == {"france", "germany"}


def test_save_uses_unique_temp_and_leaves_no_tmp(tmp_path: Path):
    path = tmp_path / "cache" / "resources.json"
    cache = ResourceLabelCache(path=path)
    cache.put_many([("entity", "france", "France")])
    assert path.is_file()
    leftovers = list(path.parent.glob("*.tmp"))
    assert leftovers == []


def test_hydrate_survives_cache_write_failure(tmp_path: Path, monkeypatch):
    cache = ResourceLabelCache(path=tmp_path / "resources.json")
    client = MagicMock()
    client.get_resources.return_value = [
        Resource(id="france", label="France"),
    ]
    workspace = Workspace(entity=["france"])
    labels: dict[str, str] = {}

    def boom(*_a, **_k):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(cache, "put_many", boom)
    cache.hydrate_workspace(client, workspace, labels)
    assert labels["france"] == "France"
