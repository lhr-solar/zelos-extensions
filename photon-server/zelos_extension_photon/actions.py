"""Photon actions, served as ``Photon/<function name>``.

Free functions over one module-level `STATE`, because dynamic ``choices=`` providers are bound at
decoration time (the same pattern as zelos-extension-can). Read-only actions are ones Zelos AI may
run without asking: List Sessions and Job Status.
"""

from __future__ import annotations

import datetime as dt
import inspect
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import zelos_sdk
from zelos_sdk.actions import ActionsRegistry, action

from .loader import (
    PHOTON_TAG,
    Cancelled,
    LiveLoader,
    Naming,
    Progress,
    build_and_upload,
    can_naming,
    open_cloud_trace,
    write_trace,
)
from .photon import CatalogSession, PhotonClient, PhotonError
from .sessions import (
    RESOLUTION_CHOICES,
    Window,
    day_from_choice,
    day_label,
    estimate_mb,
    fmt_duration,
    group_by_day,
    local_start,
    plan_window,
    resolution_level,
    session_id_from_choice,
    session_label,
    session_summary,
)
from .uploads import UploadRecord, upload_key

logger = logging.getLogger(__name__)

CATALOG_TTL_S = 60.0
LARGE_MB = 1500.0  # refuse bigger loads unless allow_large is ticked
JOB_WAIT_S = 240.0  # under the five minutes an action caller waits
# The app's agent prunes live samples older than ~8 h (by sample time) every minute.
LIVE_MAX_AGE_H = 7.5
DEST_CLOUD, DEST_FILE, DEST_LIVE = "Zelos Cloud", "Trace file", "Live workspace (recent data only)"
DESTINATIONS = [DEST_CLOUD, DEST_FILE, DEST_LIVE]


class State:
    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.progress = Progress()
        self._job_lock = threading.Lock()
        self._catalog: list[CatalogSession] = []
        self._catalog_at = 0.0
        self._catalog_lock = threading.Lock()
        self.live = LiveLoader(self.client)
        self.agent_factory = zelos_sdk.connect
        self.opener = open_cloud_trace
        self.workdir = Path(os.environ["ZELOS_DATA_DIR"]) if os.environ.get("ZELOS_DATA_DIR") else None
        # In the home folder, not the per-version extension data dir, so it survives reinstalls.
        self.uploads = UploadRecord(Path.home() / ".photon-zelos" / "uploads.json")

    @property
    def traces_dir(self) -> Path:
        configured = (self.config.get("traces_dir") or "").strip()
        return Path(configured).expanduser() if configured else Path.home() / "Documents" / "Photon Traces"

    def trace_path(self, s: CatalogSession, resolution: str, whole: bool, window: Window) -> Path:
        res = {"raw": "raw", "100 ms": "100ms", "1 s": "1s", "10 s": "10s"}.get(resolution, resolution)
        start = local_start(s, self.tz).strftime("%Y-%m-%d %H%M")
        if whole:
            return self.traces_dir / f"{start} {s.id} {res}.trz"
        w0 = dt.datetime.fromtimestamp(window.from_ms / 1000, self.tz).strftime("%H%M%S")
        return self.traces_dir / f"{start} {s.id} {res} from {w0} {window.duration_s / 60:.0f}min.trz"

    # config ------------------------------------------------------------
    def configure(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self._catalog_at = 0.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.get("timezone") or "America/Chicago")

    @property
    def naming(self) -> Naming:
        """Trace naming that matches the CAN extension's (configurable if the CAN bus is named differently)."""
        return can_naming((self.config.get("can_source") or "CAN").strip(),
                          (self.config.get("can_bus") or "CarCAN").strip())

    @property
    def default_resolution(self) -> str:
        return self.config.get("default_resolution") or "1 s"

    def client(self) -> PhotonClient:
        password = self.config.get("password") or os.environ.get("PHOTON_PASSWORD", "")
        return PhotonClient(self.config.get("photon_url") or "https://photon-dev.lhrsolar.org", password)

    # catalog -----------------------------------------------------------
    def catalog(self, refresh: bool = False) -> list[CatalogSession]:
        with self._catalog_lock:
            if refresh or not self._catalog or time.monotonic() - self._catalog_at > CATALOG_TTL_S:
                self._catalog, _ = self.client().fetch_catalog()
                self._catalog_at = time.monotonic()
            return self._catalog

    def session(self, choice: str) -> CatalogSession:
        sid = session_id_from_choice(choice)
        for s in self.catalog():
            if s.id == sid:
                return s
        for s in self.catalog(refresh=True):
            if s.id == sid:
                return s
        raise ValueError(f"Photon has no session {sid!r}. Run List Sessions to see what is stored.")

    # jobs --------------------------------------------------------------
    def run_job(self, kind: str, session_id: str, fn, wait_s: float | None = None) -> dict[str, Any]:
        """Run one job at a time on a worker thread.

        Callers of an action stop waiting after five minutes even if the action's own timeout is longer,
        so wait up to `wait_s` and otherwise return a 'running' answer; Job Status reports the outcome.
        """
        if not self._job_lock.acquire(blocking=False):
            snap = self.progress.snapshot()
            raise RuntimeError(f"Busy with {snap['job']} of {snap['session']} ({snap['phase']}). "
                               f"Wait for it or run Cancel Job.")
        self.progress.cancel.clear()
        self.progress.update(job=kind, session=session_id, phase="starting", rows=0, events=0, chunks_done=0,
                             chunks_total=0, started=time.monotonic(), detail="", result=None)
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["result"] = fn()
                self.progress.update(phase="done", result=box["result"])
            except Cancelled:
                box["error"] = RuntimeError(f"{kind} of {session_id} was cancelled")
                self.progress.update(phase="cancelled")
            except Exception as e:
                logger.exception("%s of %s failed", kind, session_id)
                box["error"] = e
                self.progress.update(phase="failed", detail=str(e)[:500])
            finally:
                self._job_lock.release()

        worker = threading.Thread(target=work, name=f"photon-{kind}", daemon=True)
        worker.start()
        worker.join(JOB_WAIT_S if wait_s is None else wait_s)
        if worker.is_alive():
            return {"status": "running", "note": "Still working. Run Job Status for progress and the result.",
                    **self.progress.snapshot()}
        if "error" in box:
            raise box["error"]
        return box["result"]


STATE = State()


# ── choice providers (run each time a form renders) ──────────────────────────


def _day_choices(*_: Any) -> list[str]:
    days = group_by_day(STATE.catalog(), STATE.tz)
    return [day_label(day, sessions) for day, sessions in days.items()]


def _session_choices(day: str | None = None, *_: Any) -> list[str]:
    days = group_by_day(STATE.catalog(), STATE.tz)
    sessions = days.get(day_from_choice(day), []) if day else [s for group in days.values() for s in group]
    return [session_label(s, STATE.tz) for s in sessions]


def _parse_list(text: str | None) -> list[str]:
    return [p.strip() for p in (text or "").replace("\n", ",").split(",") if p.strip()]


def _selection_fields(func):
    """Day -> session dropdowns plus resolution/window/signal fields shared by Load and Upload."""
    func = action.boolean("allow_large", title="Allow large transfers", required=False, default=False,
                          description=f"Load more than about {LARGE_MB / 1000:.1f} GB of CSV. "
                                      "Raw data for a whole session can be several GB.")(func)
    func = action.text("signals", title="Signals", required=False,
                       description="Optional comma-separated Message.Signal names (up to 256). "
                                   "Empty loads every signal.")(func)
    func = action.number("duration_min", title="Duration (min)", required=False, minimum=0,
                         description="Minutes to load. Empty or 0 loads to the end of the session.")(func)
    func = action.number("start_offset_min", title="Start offset (min)", required=False, minimum=0,
                         default=0, description="Minutes after the session start.")(func)
    func = action.select("resolution", title="Resolution", choices=RESOLUTION_CHOICES, required=False,
                         description="1 s for whole sessions; 100 ms for a few minutes of detail; raw for "
                                     "short windows only.")(func)
    func = action.select("session", title="Session", choices=_session_choices, depends_on="day",
                         description="Photon TSDB session (id | local start, length, samples).")(func)
    func = action.select("day", title="Day", choices=_day_choices, required=False,
                         description="Pick a day to narrow the session list.")(func)
    return func


def _prepare(session: str, resolution: str | None, start_offset_min: float | None,
             duration_min: float | None, signals: str | None, allow_large: bool | None):
    s = STATE.session(session)
    level = resolution_level(resolution, STATE.default_resolution)
    window = plan_window(s, start_offset_min, duration_min)
    est = estimate_mb(window, level)
    if est > LARGE_MB and not allow_large:
        raise ValueError(f"That is roughly {est / 1000:.1f} GB of Photon data ({fmt_duration(window.duration_s)} "
                         f"at {resolution or STATE.default_resolution}). Use a coarser resolution or a "
                         f"shorter window, or tick Allow large transfers.")
    return s, level, window, _parse_list(signals) or None


# ── actions ───────────────────────────────────────────────────────────────


@action("List Sessions",
        "Lists the car sessions stored in Photon, newest day first: id, local start time, length in "
        "minutes, sample count and whether video exists. Use a session id from here with Load Session.",
        read_only=True, timeout=60)
@action.text("day", title="Day (YYYY-MM-DD)", required=False,
             description="Only this local day. Empty lists every day.")
@action.integer("limit_days", title="Max days", required=False, default=10, minimum=1, maximum=400)
@action.boolean("refresh", title="Refresh from Photon", required=False, default=False)
def list_sessions(day: str | None = None, limit_days: int = 10, refresh: bool = False) -> dict[str, Any]:
    days = group_by_day(STATE.catalog(refresh=bool(refresh)), STATE.tz)
    out = []
    for d, sessions in days.items():
        if day and d != day.strip():
            continue
        out.append({"day": d, "session_count": len(sessions),
                    "total_min": round(sum(s.duration_s for s in sessions) / 60, 1),
                    "sessions": [session_summary(s, STATE.tz) for s in sessions]})
        if len(out) >= (limit_days or 10):
            break
    return {"timezone": str(STATE.tz), "days": out, "total_days": len(days),
            "total_sessions": sum(len(v) for v in days.values())}


@action("Load Session",
        f"Loads a Photon session (or a window of it) into Zelos. By default it uploads it to Zelos Cloud as a trace "
        f"tagged '{PHOTON_TAG}' and opens it in the app; loading the same thing again on this laptop reuses that "
        "trace. 'Trace file' saves a local .trz instead. 'Live workspace' streams into the live view, but Zelos "
        f"keeps live data for only about 8 hours, so that works only for data under {LIVE_MAX_AGE_H:g} h old. "
        "Signals use the CAN extension's names (CAN/CarCAN/<Message>.<Signal>), so CAN layouts such as "
        "High Noon work on them.",
        timeout=1800, submit_text="Load")
@action.boolean("new_upload", title="Upload again", required=False, default=False,
                description="Zelos Cloud only: upload a fresh trace even if this laptop already uploaded this one.")
@action.boolean("open_in_app", title="Open in Zelos when done", required=False, default=True,
                description="Zelos Cloud only.")
@action.text("extra_tags", title="Extra tags", required=False,
             description=f"Zelos Cloud only: comma-separated existing org tags, added to '{PHOTON_TAG}'.")
@action.text("name", title="Trace name", required=False,
             description="Zelos Cloud only. Defaults to '<local date time> <session id> (<resolution>)'.")
@action.select("destination", title="Destination", choices=DESTINATIONS, required=False,
               description="Zelos Cloud (default): shared with the lhrs org, tagged Photon. Trace file: a local "
                           f".trz. Live workspace: only data from the last {LIVE_MAX_AGE_H:g} hours.")
@_selection_fields
def load_session(session: str, day: str | None = None, resolution: str | None = None,
                 start_offset_min: float | None = 0, duration_min: float | None = None,
                 signals: str | None = None, allow_large: bool | None = False,
                 destination: str | None = None, name: str | None = None, extra_tags: str | None = None,
                 open_in_app: bool | None = True, new_upload: bool | None = False) -> dict[str, Any]:
    s, level, window, sig = _prepare(session, resolution, start_offset_min, duration_min, signals, allow_large)
    res_name = resolution or STATE.default_resolution
    dest = destination or DESTINATIONS[0]
    if dest not in DESTINATIONS:
        raise ValueError(f"Unknown destination {dest!r}. Choose one of {DESTINATIONS}")
    whole = window.from_ms == s.min_ms and window.to_ms > s.max_ms
    if dest == DEST_LIVE:
        age_h = (time.time() * 1000 - window.to_ms) / 3600_000
        if age_h > LIVE_MAX_AGE_H:
            raise ValueError(f"That window ended {age_h:.0f} h ago. Zelos drops live data older than about 8 h "
                             f"within a minute, so load it to Zelos Cloud or a Trace file instead.")

    common = {"session": s.id, "resolution": res_name, "window_start": local_start_iso(window.from_ms),
              "window_min": round(window.duration_s / 60, 1)}
    org = STATE.config.get("organization") or None

    if dest == DEST_CLOUD:
        key = upload_key(s.id, level, window.from_ms, window.to_ms, sig, org, STATE.naming.label)
        prior = None if new_upload else STATE.uploads.get(key)
        if prior and prior.get("trace_id"):
            opened = bool(open_in_app) and _open(prior.get("organization") or org, prior)
            return {**common, "destination": "zelos cloud", "reused": True, "opened_in_app": opened,
                    "note": "This laptop already uploaded this. Tick 'Upload again' for a fresh trace.", **prior}

    def job() -> dict[str, Any]:
        t0 = time.monotonic()
        if dest == DEST_LIVE:
            res = STATE.live.load(s, window, level, sig, STATE.progress)
            return {**common, "destination": "live workspace", "seconds": round(time.monotonic() - t0, 1), **res}
        if dest == DEST_FILE:
            path = STATE.trace_path(s, res_name, whole=whole, window=window)
            res = write_trace(STATE.client(), s, window, level, sig, path, STATE.progress, STATE.naming)
            return {**common, "destination": "trace file", "path": str(path),
                    "size_mb": round(path.stat().st_size / 1e6, 1), "seconds": round(time.monotonic() - t0, 1),
                    "next": "Open this .trz in Zelos: Open trace on Home, or Data Sources -> Add trace -> File.",
                    **res}
        label = name or (f"{local_start(s, STATE.tz).strftime('%Y-%m-%d %H:%M %Z')} {s.id} ({res_name})"
                         + ("" if whole else f" from {dt.datetime.fromtimestamp(window.from_ms / 1000, STATE.tz):%H:%M}"
                                             f" {window.duration_s / 60:.0f} min"))
        res = build_and_upload(STATE.client(), STATE.agent_factory(), s, window, level, sig, label,
                               _parse_list(extra_tags), org, STATE.progress, STATE.workdir, STATE.naming)
        entry = {"url": res.get("url"), "trace_id": res.get("trace_id"), "name": res.get("name"),
                 "tags": res.get("tags"), "organization": org or _org_from_url(res.get("url"))}
        if entry["trace_id"]:
            STATE.uploads.put(upload_key(s.id, level, window.from_ms, window.to_ms, sig, org, STATE.naming.label),
                              entry)
        opened = bool(open_in_app) and _open(entry["organization"], entry)
        return {**common, "destination": "zelos cloud", "reused": False, "opened_in_app": opened,
                "seconds": round(time.monotonic() - t0, 1), **res}

    return STATE.run_job("load", s.id, job)


def _open(org: str | None, entry: dict[str, Any]) -> bool:
    org = org or _org_from_url(entry.get("url"))
    return bool(org and entry.get("trace_id")) and bool(STATE.opener(org, entry["trace_id"]))


@action("Job Status", "Progress of the running (or last) load or upload.", read_only=True, timeout=10)
def job_status() -> dict[str, Any]:
    return STATE.progress.snapshot()


@action("Cancel Job", "Stops the running load or upload after its current chunk.", timeout=10)
def cancel_job() -> dict[str, Any]:
    STATE.progress.cancel.set()
    return {"cancelling": STATE.progress.snapshot()["phase"] not in ("idle", "done", "failed", "cancelled")}


def local_start_iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, STATE.tz).isoformat(timespec="seconds")


def _org_from_url(url: str | None) -> str | None:
    # https://console2.zeloscloud.io/<org>/traces/<id>
    parts = (url or "").split("/")
    return parts[3] if len(parts) > 4 and parts[4] == "traces" else None


def register_actions(registry: ActionsRegistry) -> list[str]:
    module = sys.modules[__name__]
    names = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("_") and hasattr(obj, "_action"):
            registry.register(obj, name=name)
            names.append(name)
    return names


__all__ = ["STATE", "PhotonError", "register_actions"]
