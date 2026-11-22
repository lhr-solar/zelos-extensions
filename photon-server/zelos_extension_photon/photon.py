"""Client for the Photon web UI's read-only interfaces.

- Session catalog: protocol v1 over ``wss://<host>/ws`` (``hello`` -> ``hello_ack``, ``catalog_list`` ->
  ``catalog_result``). See photon-next/docs/protocol.md in lhr-solar/photon-mono.
- Data: ``GET /export.csv?session=&from=&to=&level=&signals=`` streams long-format rows
  ``t_ms,message,signal,unit,value`` straight from the TSDB.

Both sit behind HTTP Basic auth (user ``photon``). This client only ever sends ``hello``,
``heartbeat`` and ``catalog_list``: never ``select_source``, ``select_dbc`` or ``timeline_control``,
which would change the shared backend for every Photon viewer.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from websockets.sync.client import connect as ws_connect

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
CLIENT_NAME = "zelos-extension-photon"


class PhotonError(RuntimeError):
    """Photon could not be reached, refused the password, or returned something unexpected."""


@dataclass(frozen=True, slots=True)
class Row:
    t_ms: int
    message: str
    signal: str
    unit: str
    value: float


@dataclass(frozen=True, slots=True)
class CatalogSession:
    id: str
    min_ms: int
    max_ms: int
    sample_count: int
    has_video: bool
    dbc_hash: str

    @property
    def duration_s(self) -> float:
        return max(0, self.max_ms - self.min_ms) / 1000.0


class PhotonClient:
    def __init__(self, base_url: str, password: str, *, timeout_s: float = 20.0, retries: int = 3) -> None:
        if not password:
            raise PhotonError("Photon password is not set. Add it in the Photon extension settings.")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        token = base64.b64encode(f"photon:{password}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}
        self._http = requests.Session()
        self._http.headers.update(self._auth)
        self._http.headers["User-Agent"] = CLIENT_NAME

    # ── catalog ────────────────────────────────────────────────────────────

    @property
    def ws_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, "/ws", "", ""))

    def fetch_catalog(self) -> tuple[list[CatalogSession], dict[str, Any]]:
        """Return (TSDB sessions sorted by start time, the raw hello_ack)."""
        try:
            with ws_connect(self.ws_url, additional_headers=self._auth, open_timeout=self.timeout_s,
                            max_size=64 * 1024 * 1024, user_agent_header=CLIENT_NAME) as ws:
                ws.send(json.dumps({"type": "hello", "protocol_version": PROTOCOL_VERSION,
                                    "client": CLIENT_NAME}))
                hello_ack: dict[str, Any] | None = None
                deadline = time.monotonic() + self.timeout_s
                while time.monotonic() < deadline:
                    msg = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                    if isinstance(msg, bytes):
                        continue  # TimelineStatus and other binary frames
                    obj = json.loads(msg)
                    typ = obj.get("type")
                    if typ == "hello_ack":
                        if obj.get("protocol_version") != PROTOCOL_VERSION:
                            raise PhotonError(f"Photon speaks protocol {obj.get('protocol_version')}, "
                                              f"this extension speaks {PROTOCOL_VERSION}")
                        hello_ack = obj
                        ws.send(json.dumps({"type": "catalog_list"}))
                    elif typ == "catalog_result":
                        return parse_catalog(obj), hello_ack or {}
                    elif typ == "error_alert":
                        raise PhotonError(f"Photon error {obj.get('code')}: {obj.get('message')}")
                raise PhotonError("Photon did not answer catalog_list in time")
        except PhotonError:
            raise
        except Exception as e:  # websockets raises InvalidStatus(401) etc.
            text = str(e)
            if "401" in text:
                raise PhotonError("Photon refused the password (HTTP 401)") from e
            raise PhotonError(f"Could not reach Photon at {self.ws_url}: {text}") from e

    # ── data ───────────────────────────────────────────────────────────────

    def stream_rows(self, session_id: str, from_ms: int, to_ms: int, level: int,
                    signals: list[str] | None = None) -> Iterator[Row]:
        """Stream one window. Retries the whole window on a connection error before any row was read."""
        params = {"session": session_id, "from": from_ms, "to": to_ms, "level": level,
                  "signals": ",".join(signals) if signals else "all"}
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            yielded = False
            try:
                with self._http.get(f"{self.base_url}/export.csv", params=params, stream=True,
                                    timeout=(self.timeout_s, 120)) as r:
                    if r.status_code == 401:
                        raise PhotonError("Photon refused the password (HTTP 401)")
                    if r.status_code != 200:
                        raise PhotonError(f"export.csv {r.status_code}: {r.text[:200]}")
                    lines = r.iter_lines()
                    header = next(lines, b"")
                    if not header.startswith(b"t_ms,message,signal"):
                        raise PhotonError(f"Unexpected export.csv header: {header[:80]!r}")
                    for line in lines:
                        row = parse_row(line)
                        if row is not None:
                            yielded = True
                            yield row
                return
            except PhotonError:
                raise
            except requests.RequestException as e:
                if yielded:
                    raise PhotonError(f"Photon export broke mid-stream: {e}") from e
                last = e
                logger.warning("export.csv attempt %d/%d failed: %s", attempt, self.retries, e)
                time.sleep(min(2.0 * attempt, 5.0))
        raise PhotonError(f"Photon export failed after {self.retries} attempts: {last}")


def parse_catalog(obj: dict[str, Any]) -> list[CatalogSession]:
    out = []
    for s in obj.get("sessions", []):
        try:
            out.append(CatalogSession(
                id=str(s["id"]), min_ms=int(s["min_ms"]), max_ms=int(s["max_ms"]),
                sample_count=int(s.get("sample_count", 0)), has_video=bool(s.get("has_video", False)),
                dbc_hash=str(s.get("dbc_hash", "") or "")))
        except (KeyError, TypeError, ValueError):
            logger.warning("skipping malformed catalog row: %r", s)
    out.sort(key=lambda c: c.min_ms)
    return out


def parse_row(line: bytes) -> Row | None:
    """Parse one ``t_ms,message,signal,unit,value`` line; None for blank or non-numeric rows."""
    if not line:
        return None
    parts = line.decode("utf-8", "replace").split(",", 4)
    if len(parts) != 5:
        return None
    t, msg, sig, unit, val = parts
    try:
        return Row(int(t), msg, sig, unit, float(val))
    except ValueError:
        return None
