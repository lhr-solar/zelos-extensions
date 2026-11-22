import json
from pathlib import Path

import pytest

from zelos_extension_photon.photon import CatalogSession, Row, parse_catalog, parse_row

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def catalog() -> list[CatalogSession]:
    return parse_catalog(json.loads((FIX / "catalog_result.json").read_text()))


@pytest.fixture
def csv_rows() -> list[Row]:
    """200 real L0 rows from /export.csv (one signal of BPS_Module_Override)."""
    lines = (FIX / "csv_20260723-164704-00_L0.head.csv").read_bytes().splitlines()[1:]
    return [r for r in map(parse_row, lines) if r is not None]


class FakeClient:
    """Stands in for PhotonClient: serves rows from memory, filtered by window and level."""

    def __init__(self, rows_by_level: dict[int, list[Row]]):
        self.rows_by_level = rows_by_level
        self.calls: list[tuple] = []

    def stream_rows(self, session_id, from_ms, to_ms, level, signals=None):
        self.calls.append((session_id, from_ms, to_ms, level, tuple(signals or ())))
        for r in self.rows_by_level.get(level, []):
            if from_ms <= r.t_ms < to_ms and (not signals or f"{r.message}.{r.signal}" in signals):
                yield r


class FakeSource:
    def __init__(self):
        self.events: dict[str, list] = {}
        self.records: list = []
        self.flushes = 0

    def add_event(self, name, schema):
        if name in self.events:
            raise ValueError(f"Event={name} already exists")
        self.events[name] = [(f.name, f.unit) if hasattr(f, "unit") else f for f in schema]

    def log_many(self, records):
        self.records.extend(records)

    def flush(self):
        self.flushes += 1
