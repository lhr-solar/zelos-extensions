---
description: Live wheel speed (front-left, front-right) and motor-controller vehicle speed in mph, the way Photon shows them.
authors:
- Ravi D Shah
dependencies:
- zelos-sdk[notebook]
params:
  poll_seconds: 0.2
  window: -2m
  can_prefix: CAN/CarCAN
  source_suffix: _LilShah
---

Photon shows speed in mph two ways, and so does the **High Noon → Dynamics** tab: wheel speed from `WheelRPM_FL` / `WheelRPM_FR` (rpm on a 21.56 in rolling diameter) and vehicle speed from the motor controller's `MC_VelocityMeasurement.MC_VehicleVelocity` (m/s). This notebook converts the live CAN signals into one source, the same one the Photon extension writes into Photon traces, so the tab works live and on history:

| Source | Event | Fields |
|---|---|---|
| `Speed_LilShah` | `Speed` | wheel_FL, wheel_FR, vehicle (mph) |

The wheel messages are decoded only when the CAN extension has `dbc/VCAT-Sensors.dbc` from the zelos-extensions repo in its database files. Values keep their CAN timestamps; polling re-reads a one-second overlap and skips samples it has already written.

```python {#7c2e9b01}
from datetime import datetime, timezone
import math
import threading
import zelos_sdk
from zelos_sdk import connect

agent = connect()
zelos_sdk.init(url=agent.target)
F, T = zelos_sdk.TraceEventFieldMetadata, zelos_sdk.DataType

WHEEL_DIAMETER_IN = 21.56  # Photon's rolling diameter (photon-mono docs/mechanical/dynamics.md)
MPH_PER_RPM = math.pi * WHEEL_DIAMETER_IN * 60 / 63_360
MPH_PER_MPS = 3600 / 1609.344
INPUTS = {  # field -> (Message.Signal, scale to mph); same as SpeedDeriver in the Photon extension
    "wheel_FL": ("WheelRPM_FL.RPM", MPH_PER_RPM),
    "wheel_FR": ("WheelRPM_FR.RPM", MPH_PER_RPM),
    "vehicle": ("MC_VelocityMeasurement.MC_VehicleVelocity", MPH_PER_MPS),
}
speed = zelos_sdk.TraceSource("Speed" + params.source_suffix).add_event(
    "Speed", [F(f, T.Float64, "mph") for f in INPUTS])
state = {"last_ns": dict.fromkeys(INPUTS, 0), "seen": {f: set() for f in INPUTS}, "samples": 0, "errors": 0,
         "last_error": None, "worker": None}
stop_event = threading.Event()


def series(field, start):
    p = f"{params.can_prefix}/{INPUTS[field][0]}"
    df = agent.query([p], start=start).to_pandas().sort_index()
    return df[p].dropna() if p in df else df.iloc[:, :0]


def publish(field, start):
    n = 0
    for ts, x in series(field, start).items():
        t = int(ts.value)
        if t in state["seen"][field]:
            continue
        speed.log_at(t, **{field: float(x) * INPUTS[field][1]})
        state["seen"][field].add(t)
        state["last_ns"][field] = max(state["last_ns"][field], t)
        n += 1
    if len(state["seen"][field]) > 20000:
        cutoff = state["last_ns"][field] - 10_000_000_000
        state["seen"][field] = {t for t in state["seen"][field] if t >= cutoff}
    return n


# Seed from the recent window so the panels fill immediately.
seeded = {f: publish(f, params.window) for f in INPUTS}
{"seeded": seeded}
```

```python {#7c2e9b02}
def pump_speed():
    while not stop_event.is_set():
        for f in INPUTS:
            try:
                last = state["last_ns"][f]
                start = (datetime.fromtimestamp(max(0, last - 1_000_000_000) / 1e9, tz=timezone.utc)
                         if last else params.window)
                state["samples"] += publish(f, start)
                state["last_error"] = None
            except Exception as exc:
                state["errors"] += 1
                state["last_error"] = repr(exc)
        stop_event.wait(params.poll_seconds)


def start_speed():
    worker = state.get("worker")
    if worker is not None and worker.is_alive():
        return "already running"
    stop_event.clear()
    worker = threading.Thread(target=pump_speed, name="speed-mph-lilshah", daemon=True)
    state["worker"] = worker
    worker.start()
    return "started"


def stop_speed():
    stop_event.set()
    worker = state.get("worker")
    if worker is not None:
        worker.join(timeout=3)
    return {"running": bool(worker and worker.is_alive()), "samples_published": state["samples"],
            "errors": state["errors"], "last_error": state["last_error"]}


start_speed()
```

```python {#7c2e9b03}
{"running": bool(state["worker"] and state["worker"].is_alive()), "samples_published": state["samples"],
 "errors": state["errors"], "last_error": state["last_error"], "poll_seconds": params.poll_seconds}
```
