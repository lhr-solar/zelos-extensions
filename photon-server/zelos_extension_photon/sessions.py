"""Session catalog helpers: day grouping, human labels, resolutions and time windows."""

from __future__ import annotations

import datetime as dt
from collections import OrderedDict
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .photon import CatalogSession

# Photon TSDB levels as /export.csv names them (measured in the Phase 0 spike).
RESOLUTIONS: dict[str, int] = {"raw": 0, "100 ms": 1, "1 s": 2, "10 s": 3}
RESOLUTION_CHOICES = ["1 s", "100 ms", "raw", "10 s"]
# Coarsest level, used to discover which messages/signals a session holds before declaring schemas.
DISCOVERY_LEVEL = 5

# Window size per request, by level, so one request stays tens of MB and a failure costs little.
_CHUNK_MS = {0: 60_000, 1: 10 * 60_000, 2: 60 * 60_000, 3: 6 * 3600_000}


@dataclass(frozen=True, slots=True)
class Window:
    from_ms: int
    to_ms: int

    @property
    def duration_s(self) -> float:
        return (self.to_ms - self.from_ms) / 1000.0


def resolution_level(name: str | None, default: str = "1 s") -> int:
    key = (name or default).strip()
    if key not in RESOLUTIONS:
        raise ValueError(f"Unknown resolution {key!r}. Choose one of {RESOLUTION_CHOICES}")
    return RESOLUTIONS[key]


def local_start(s: CatalogSession, tz: ZoneInfo) -> dt.datetime:
    return dt.datetime.fromtimestamp(s.min_ms / 1000, tz)


def group_by_day(sessions: list[CatalogSession], tz: ZoneInfo) -> OrderedDict[str, list[CatalogSession]]:
    """Newest day first; sessions within a day in start order."""
    days: dict[str, list[CatalogSession]] = {}
    for s in sessions:
        days.setdefault(local_start(s, tz).strftime("%Y-%m-%d"), []).append(s)
    out: OrderedDict[str, list[CatalogSession]] = OrderedDict()
    for day in sorted(days, reverse=True):
        out[day] = sorted(days[day], key=lambda s: s.min_ms)
    return out


def fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def fmt_count(n: int) -> str:
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{suffix}"
    return str(n)


def day_label(day: str, sessions: list[CatalogSession]) -> str:
    total = sum(s.duration_s for s in sessions)
    n = len(sessions)
    return f"{day} ({n} session{'s' if n != 1 else ''}, {fmt_duration(total)})"


def session_label(s: CatalogSession, tz: ZoneInfo) -> str:
    """Dropdown value. Always starts with the session id, which `session_id_from_choice` reads back."""
    start = local_start(s, tz).strftime("%H:%M %Z")
    video = ", video" if s.has_video else ""
    return f"{s.id} | {start}, {fmt_duration(s.duration_s)}, {fmt_count(s.sample_count)} samples{video}"


def day_from_choice(choice: str) -> str:
    return choice.split(" ", 1)[0].strip()


def session_id_from_choice(choice: str) -> str:
    return choice.split("|", 1)[0].strip()


def session_summary(s: CatalogSession, tz: ZoneInfo) -> dict:
    start = local_start(s, tz)
    return {
        "id": s.id,
        "start_local": start.isoformat(timespec="seconds"),
        "start_utc_ms": s.min_ms,
        "end_utc_ms": s.max_ms,
        "duration_min": round(s.duration_s / 60, 1),
        "samples": s.sample_count,
        "has_video": s.has_video,
    }


def plan_window(s: CatalogSession, start_offset_min: float | None, duration_min: float | None) -> Window:
    """Clamp an optional (offset, duration) in minutes to the session's span."""
    off = max(0.0, float(start_offset_min or 0.0))
    frm = s.min_ms + int(off * 60_000)
    if frm >= s.max_ms:
        raise ValueError(f"Start offset {off:g} min is past the end of {s.id} "
                         f"({fmt_duration(s.duration_s)} long)")
    to = s.max_ms if not duration_min or duration_min <= 0 else min(s.max_ms, frm + int(duration_min * 60_000))
    # +1 ms keeps the session's final sample whether /export.csv treats `to` as inclusive or exclusive.
    return Window(frm, to + 1 if to == s.max_ms else to)


def chunks(window: Window, level: int) -> list[Window]:
    step = _CHUNK_MS.get(level, 24 * 3600_000)
    out, t = [], window.from_ms
    while t < window.to_ms:
        out.append(Window(t, min(window.to_ms, t + step)))
        t += step
    return out


def estimate_mb(window: Window, level: int) -> float:
    """Rough CSV size from the spike (316 signals, busiest session); good enough for warnings."""
    per_5min = {0: 143.5, 1: 21.6, 2: 4.6, 3: 0.53}.get(level, 0.1)
    return per_5min * window.duration_s / 300.0
