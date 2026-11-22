from zoneinfo import ZoneInfo

import pytest

from zelos_extension_photon.photon import CatalogSession
from zelos_extension_photon.sessions import (
    chunks,
    day_from_choice,
    day_label,
    group_by_day,
    plan_window,
    resolution_level,
    session_id_from_choice,
    session_label,
)

CHI = ZoneInfo("America/Chicago")


def test_catalog_fixture_parses_and_sorts(catalog):
    assert len(catalog) == 320
    assert [s.min_ms for s in catalog] == sorted(s.min_ms for s in catalog)
    assert catalog[-1].id == "20261017-020832-00"


def test_days_are_local_and_newest_first(catalog):
    days = group_by_day(catalog, CHI)
    keys = list(days)
    assert keys == sorted(keys, reverse=True)
    # 20261017-012401-00 starts 01:24 UTC = 20:24 CDT the previous evening
    assert any(s.id == "20261017-012401-00" for s in days["2026-10-16"])


def test_labels_round_trip_to_ids(catalog):
    s = next(c for c in catalog if c.id == "20260723-164704-00")
    label = session_label(s, CHI)
    assert label.startswith("20260723-164704-00 | 11:47 CDT, 3.6 h, 120.0M samples")
    assert session_id_from_choice(label) == s.id
    days = group_by_day(catalog, CHI)
    assert day_from_choice(day_label("2026-07-23", days["2026-07-23"])) == "2026-07-23"


def test_resolution_names():
    assert [resolution_level(n) for n in ("raw", "100 ms", "1 s", "10 s")] == [0, 1, 2, 3]
    assert resolution_level(None, default="100 ms") == 1
    with pytest.raises(ValueError):
        resolution_level("5 s")


def _session(minutes: float) -> CatalogSession:
    return CatalogSession("x", 1_000_000, 1_000_000 + int(minutes * 60_000), 0, False, "")


def test_window_defaults_to_whole_session_including_last_sample():
    w = plan_window(_session(30), None, None)
    assert (w.from_ms, w.to_ms) == (1_000_000, 1_000_000 + 30 * 60_000 + 1)


def test_window_offset_and_duration_are_clamped():
    w = plan_window(_session(30), 10, 60)
    assert w.from_ms == 1_000_000 + 10 * 60_000
    assert w.to_ms == 1_000_000 + 30 * 60_000 + 1
    w = plan_window(_session(30), 5, 2)
    assert w.duration_s == 120
    with pytest.raises(ValueError, match="past the end"):
        plan_window(_session(30), 31, None)


def test_chunks_cover_window_exactly():
    w = plan_window(_session(25), None, None)
    parts = chunks(w, 1)  # 10-minute chunks at 100 ms
    assert len(parts) == 3
    assert parts[0].from_ms == w.from_ms and parts[-1].to_ms == w.to_ms
    assert all(a.to_ms == b.from_ms for a, b in zip(parts, parts[1:], strict=False))
    assert len(chunks(w, 0)) == 26  # 1-minute chunks raw
