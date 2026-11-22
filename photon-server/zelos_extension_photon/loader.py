"""Move Photon rows into Zelos: into the live workspace, or into a sealed trace uploaded to Cloud.

Mapping: one Zelos event per CAN message, one Float64 field per signal, with the DBC unit. Traces use
the CAN extension's naming (source ``CAN``, event ``CarCAN/<Message>``, event type
``zelos.can.message.v1``), so one layout such as High Noon works on live PCAN data and on Photon
traces. Live-workspace loads keep their own ``photon-<session>`` source so they never mix with a
running CAN extension. Within a
message every signal shares the frame timestamp (checked across all messages in the Phase 0 spike),
so rows group cleanly by (message, t_ms). A Zelos event schema cannot grow after it is declared, so
each job first discovers the session's full message/signal set from the coarsest TSDB level.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import zelos_sdk
from zelos_sdk import DataType, TraceEventFieldMetadata

from .photon import CatalogSession, PhotonClient, Row
from .sessions import DISCOVERY_LEVEL, Window, chunks

logger = logging.getLogger(__name__)

# Every upload carries this tag. Zelos never creates tags on upload, so it must exist in the org.
PHOTON_TAG = "Photon"
LOG_BATCH = 5000

Schema = dict[str, dict[str, str]]  # message -> {signal: unit}

CAN_MESSAGE_EVENT_TYPE = "zelos.can.message.v1"


@dataclass(frozen=True)
class Naming:
    """Where Photon messages land in a trace: source, optional event prefix (the CAN bus), event type."""

    source: str
    bus: str | None = None
    event_type: str | None = None

    def event(self, message: str) -> str:
        ev = zelos_sdk.sanitize_name(message, kind="event")
        return f"{self.bus}/{ev}" if self.bus else ev

    @property
    def label(self) -> str:
        return f"{self.source}/{self.bus}" if self.bus else self.source


def can_naming(source: str = "CAN", bus: str = "CarCAN") -> Naming:
    """The CAN extension's layout: source `CAN`, events `<bus>/<Message>`, tagged as CAN messages."""
    return Naming(source or "CAN", bus or None, CAN_MESSAGE_EVENT_TYPE)


class Cancelled(Exception):
    pass


class Source(Protocol):
    def add_event(self, name: str, schema: list[TraceEventFieldMetadata], event_type: str | None = None) -> Any: ...
    def log_many(self, records: list[tuple[int, str, dict[str, float]]]) -> Any: ...
    def flush(self) -> Any: ...


@dataclass
class Progress:
    """Shared, thread-safe view of the running job for the Status action."""

    job: str = ""
    session: str = ""
    phase: str = "idle"
    chunks_done: int = 0
    chunks_total: int = 0
    rows: int = 0
    events: int = 0
    started: float = 0.0
    detail: str = ""
    result: dict[str, Any] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def check(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("cancelled")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"job": self.job, "session": self.session, "phase": self.phase,
                    "chunks": f"{self.chunks_done}/{self.chunks_total}", "rows": self.rows,
                    "events": self.events, "detail": self.detail, "result": self.result,
                    "elapsed_s": round(time.monotonic() - self.started, 1) if self.started else 0.0}


def sanitize(name: str, kind: str) -> str:
    return zelos_sdk.sanitize_name(name, kind=kind)


def discover_schema(client: PhotonClient, s: CatalogSession, signals: list[str] | None,
                    progress: Progress | None = None) -> Schema:
    schema: Schema = {}
    for row in client.stream_rows(s.id, s.min_ms, s.max_ms + 1, DISCOVERY_LEVEL, signals):
        schema.setdefault(row.message, {}).setdefault(row.signal, row.unit)
        if progress is not None and len(schema) % 16 == 0:
            progress.check()
    return schema


def declare(source: Source, schema: Schema, declared: set[str] | None = None,
            naming: Naming | None = None) -> set[str]:
    """Declare one event per message. Returns the event names declared now."""
    naming = naming or Naming("photon")
    declared = declared if declared is not None else set()
    new = set()
    for msg in sorted(schema):
        ev = naming.event(msg)
        if ev in declared:
            continue
        fields = [TraceEventFieldMetadata(sanitize(sig, "field"), DataType.Float64, unit or None)
                  for sig, unit in sorted(schema[msg].items())]
        if naming.event_type:
            source.add_event(ev, fields, event_type=naming.event_type)
        else:
            source.add_event(ev, fields)
        declared.add(ev)
        new.add(ev)
    return new


def group_rows(rows: Iterable[Row], allowed: dict[str, set[str]] | None = None, naming: Naming | None = None
               ) -> tuple[list[tuple[int, str, dict[str, float]]], int, int]:
    """Group long-format rows into (time_ns, event, {field: value}) records, sorted by time.

    `allowed` (event -> declared fields) drops anything a declared schema can't hold.
    Returns (records, rows_seen, rows_dropped).
    """
    naming = naming or Naming("photon")
    events: dict[tuple[str, int], dict[str, float]] = {}
    names: dict[tuple[str, str], tuple[str, str]] = {}
    seen = dropped = 0
    for r in rows:
        seen += 1
        key = (r.message, r.signal)
        ev_field = names.get(key)
        if ev_field is None:
            ev_field = names[key] = (naming.event(r.message), sanitize(r.signal, "field"))
        ev, fld = ev_field
        if allowed is not None and fld not in allowed.get(ev, ()):
            dropped += 1
            continue
        events.setdefault((ev, r.t_ms), {})[fld] = r.value
    recs = [(t * 1_000_000, ev, f) for (ev, t), f in events.items()]
    recs.sort(key=lambda x: x[0])
    return recs, seen, dropped


def pump(client: PhotonClient, s: CatalogSession, window: Window, level: int, signals: list[str] | None,
         source: Source, allowed: dict[str, set[str]], progress: Progress,
         naming: Naming | None = None) -> dict[str, int]:
    """Fetch `window` chunk by chunk and log it into `source`."""
    parts = chunks(window, level)
    progress.update(chunks_total=len(parts), chunks_done=0)
    totals = {"rows": 0, "events": 0, "dropped": 0}
    for i, part in enumerate(parts):
        progress.check()
        progress.update(phase=f"loading {i + 1}/{len(parts)}")
        recs, seen, dropped = group_rows(client.stream_rows(s.id, part.from_ms, part.to_ms, level, signals),
                                         allowed, naming)
        for j in range(0, len(recs), LOG_BATCH):
            progress.check()
            source.log_many(recs[j:j + LOG_BATCH])
        source.flush()
        totals["rows"] += seen
        totals["events"] += len(recs)
        totals["dropped"] += dropped
        progress.update(chunks_done=i + 1, rows=totals["rows"], events=totals["events"])
    return totals


def allowed_fields(schema: Schema, naming: Naming | None = None) -> dict[str, set[str]]:
    naming = naming or Naming("photon")
    return {naming.event(m): {sanitize(s, "field") for s in sigs} for m, sigs in schema.items()}


# ── live workspace ─────────────────────────────────────────────────────────


class LiveLoader:
    """Loads recent sessions into the agent's live workspace, one trace source per Photon session.

    Only for data younger than the app's live retention (about 8 hours, see `write_trace`).

    A per-session source (``photon-<session id>``) means every load gets a schema that matches its
    session exactly, and several loaded sessions stay distinguishable by signal path.
    """

    def __init__(self, client_factory: Callable[[], PhotonClient],
                 source_factory: Callable[[str], Source] | None = None) -> None:
        self.client_factory = client_factory
        self.source_factory = source_factory or (lambda name: zelos_sdk.TraceSource(name))
        self._sources: dict[str, tuple[Source, set[str], dict[str, set[str]]]] = {}

    @staticmethod
    def source_name(session_id: str) -> str:
        return sanitize(f"photon-{session_id}", "source")

    def load(self, s: CatalogSession, window: Window, level: int, signals: list[str] | None,
             progress: Progress) -> dict[str, Any]:
        client = self.client_factory()
        name = self.source_name(s.id)
        progress.update(phase="discovering signals")
        entry = self._sources.get(name)
        if entry is None:
            schema = discover_schema(client, s, signals, progress)
            if not schema:
                raise ValueError(f"Photon returned no data for session {s.id}")
            source = self.source_factory(name)
            declared = declare(source, schema)
            entry = (source, declared, allowed_fields(schema))
            self._sources[name] = entry
        source, _declared, allowed = entry
        totals = pump(client, s, window, level, signals, source, allowed, progress)
        return {"source": name, **totals}


# ── trace files ────────────────────────────────────────────────────────────
#
# The Zelos app's agent prunes its live (memory) store of samples older than about 8 hours, by sample
# timestamp, every minute. Photon history is weeks to months old, so it has to live in a trace file.


def write_trace(client: PhotonClient, s: CatalogSession, window: Window, level: int, signals: list[str] | None,
                path: Path, progress: Progress, naming: Naming | None = None) -> dict[str, int]:
    """Write `window` of `s` to a .trz file, named like the CAN extension by default. Nothing reaches the
    live workspace: the source lives in its own namespace, which only the TraceWriter records."""
    naming = naming or can_naming()
    progress.update(phase="discovering signals")
    schema = discover_schema(client, s, signals, progress)
    if not schema:
        raise ValueError(f"Photon returned no data for session {s.id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()  # TraceWriter refuses an existing file
    ns = zelos_sdk.TraceNamespace(f"photon_{int(time.time() * 1000)}")
    try:
        with zelos_sdk.TraceWriter(str(path), namespace=ns):
            source = zelos_sdk.TraceSource(naming.source, namespace=ns)
            declare(source, schema, naming=naming)
            return pump(client, s, window, level, signals, source, allowed_fields(schema, naming), progress, naming)
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()  # never leave a half-written trace that looks complete
        raise


def open_cloud_trace(organization: str, trace_id: str) -> bool:
    """Ask the Zelos app to open a Cloud trace through its registered zelos:// link."""
    url = f"zelos://open?org={organization.lower()}&trace={trace_id.lower()}"
    try:
        if sys.platform == "win32":
            os.startfile(url)  # noqa: S606 - the app registers the zelos:// scheme
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception as e:
        logger.warning("could not open %s: %s", url, e)
        return False


# ── Cloud upload ───────────────────────────────────────────────────────────


def build_and_upload(client: PhotonClient, agent: Any, s: CatalogSession, window: Window, level: int,
                     signals: list[str] | None, name: str, extra_tags: list[str], organization: str | None,
                     progress: Progress, workdir: Path | None = None, naming: Naming | None = None) -> dict[str, Any]:
    """Photon CSV -> TraceWriter (.trz) -> agent export to sealed TRZ2 -> agent.upload, tagged Photon.

    The app's agent keeps live data in memory, which cannot seal TRZ2, so the trace is written to
    a file in its own namespace (nothing reaches the live workspace) and sealed from that file.
    """
    tags = [PHOTON_TAG] + [t for t in extra_tags if t and t.lower() != PHOTON_TAG.lower()]
    tmp = Path(tempfile.mkdtemp(prefix="photon-upload-", dir=workdir))
    legacy, sealed = tmp / f"{s.id}.trz", tmp / f"{s.id}.trz2.trz"
    try:
        totals = write_trace(client, s, window, level, signals, legacy, progress, naming)
        progress.check()
        progress.update(phase="sealing TRZ2")
        trace = agent.trace(str(legacy))
        trace.export(str(sealed), format="trz2", overwrite=True)
        with contextlib.suppress(Exception):
            agent.close_traces([str(legacy)])
        progress.check()
        progress.update(phase="uploading")
        try:
            up = agent.upload(path=str(sealed), name=name, tags=tags, organization=organization or None,
                              timeout=1800)
        except ValueError as e:
            if "unknown tag" in str(e).lower():
                raise ValueError(
                    f"Zelos Cloud has no '{PHOTON_TAG}' tag (or another tag you named) in this organization. "
                    f"An org owner must create it once in console2.zeloscloud.io -> Tags, then retry. "
                    f"Agent said: {e}") from e
            raise
        return {"url": getattr(up, "url", None), "trace_id": getattr(up, "trace_id", None), "name": name,
                "tags": tags, "sealed_mb": round(_size(sealed) / 1e6, 2), **totals}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _size(p: Path) -> int:
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return p.stat().st_size if p.exists() else 0
