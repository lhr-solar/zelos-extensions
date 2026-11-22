"""This laptop's record of what it already uploaded, so loading the same thing again reuses that trace.

Zelos exposes no way to list or search Cloud traces, so the record is per laptop: a teammate who
uploads the same session gets their own trace. Trace names follow one convention
('<local date time> <session id> (<resolution>)') so duplicates are easy to spot in the console.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def upload_key(session_id: str, level: int, from_ms: int, to_ms: int, signals: list[str] | None,
               organization: str | None, naming: str = "") -> str:
    """Identifies one upload. `naming` (e.g. 'CAN/CarCAN') keeps traces written with older signal names
    from being reused once the naming changes."""
    sig = ",".join(sorted(signals)) if signals else "all"
    return f"{organization or '-'}|{naming or 'photon'}|{session_id}|L{level}|{from_ms}-{to_ms}|{sig}"


class UploadRecord:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            logger.warning("ignoring unreadable upload record %s: %s", self.path, e)
            return {}

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read().get(key)

    def put(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data[key] = {**entry, "uploaded_at": int(time.time())}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    def forget(self, key: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(key, None) is not None:
                self.path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
