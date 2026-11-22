---
description: Live per-tap BPS cell voltage, temperature and thermistor ADC, plus pack min/max/spread summaries, split from the lilShah DBC aggregate arrays.
authors:
- Ravi D Shah
dependencies:
- zelos-sdk[notebook]
params:
  poll_seconds: 0.1
  window: -2m
  can_prefix: CAN/CarCAN
  source_suffix: _LilShah
---

The BPS reports its 32 voltage taps and 32 temperature taps one at a time: `BPS_Voltage_Aggregate_Arr`, `BPS_Temperature_Aggregate_Arr` and `BPS_Temp_ADC_Aggregate_Arr` each carry a `BPS_Tap_idx` plus that tap's values. This notebook splits the live CAN data into one event per tap and keeps a pack summary, the way the PDU Channel Live Splitter does for the PDU. It writes exactly the sources the Photon extension writes into Photon traces, so the **High Noon → Battery** tab shows live data while this runs and historical data on any Photon trace:

| Source | Events | Fields |
|---|---|---|
| `BPSVoltageTaps_LilShah` | `Tap_00`–`Tap_31` | voltage (V), fault (DBC label), age (ms) |
| `BPSVoltageOverall_LilShah` | `Overall` | min/max_voltage, spread, min_tap, max_tap, faulted_taps, taps_reporting |
| `BPSTemperatureTaps_LilShah` | `Tap_00`–`Tap_31` | temperature (°C), fault (DBC label), age (ms) |
| `BPSTemperatureOverall_LilShah` | `Overall` | min/max_temperature, spread, min_tap, max_tap, faulted_taps, taps_reporting |
| `BPSTempADCTaps_LilShah` | `Tap_00`–`Tap_31` | adc |

The summaries use every tap's latest value. Values keep their CAN timestamps; polling re-reads a one-second overlap and skips frames it has already written.

```python {#5b1d6a01}
from datetime import datetime, timezone
import math
import threading
import zelos_sdk
from zelos_sdk import connect

agent = connect()
zelos_sdk.init(url=agent.target)
F, T = zelos_sdk.TraceEventFieldMetadata, zelos_sdk.DataType
TAPS = 32

# Fault labels: VAL_ 11 / VAL_ 12 in lilShah-finalv2usethis.dbc (same tables as the Photon extension).
VOLTAGE_FAULTS = {0: "OK", 1: "BQ I2C Read Error", 2: "Out-of-Bounds", 3: "Over-Voltage", 4: "Under-Voltage",
                  5: "Message Watchdog"}
TEMPERATURE_FAULTS = {0: "OK", 1: "Out-of-Bounds (Short to GND)", 2: "Out-of-Bounds (Short to VCC)",
                      3: "Disconnected", 4: "Over-Temperature", 5: "Charge Over-Temperature",
                      6: "Under-Temperature", 7: "Charge Under-Temperature", 8: "Message Watchdog"}

ARRAYS = {
    "voltage": dict(message="BPS_Voltage_Aggregate_Arr", data="BPS_Voltage_Tap_Data", fault="BPS_Voltage_Tap_Fault",
                    age="BPS_Voltage_Tap_Age", unit="V", labels=VOLTAGE_FAULTS, taps="BPSVoltageTaps",
                    overall="BPSVoltageOverall"),
    "temperature": dict(message="BPS_Temperature_Aggregate_Arr", data="BPS_Temperature_Tap_Data",
                        fault="BPS_Temperature_Tap_Fault", age="BPS_Temperature_Tap_Age", unit="°C",
                        labels=TEMPERATURE_FAULTS, taps="BPSTemperatureTaps", overall="BPSTemperatureOverall"),
    "adc": dict(message="BPS_Temp_ADC_Aggregate_Arr", data="BPS_Temperature_Tap_ADC", taps="BPSTempADCTaps"),
}


def path(spec, signal):
    return f"{params.can_prefix}/{spec['message']}.{signal}"


def label(table, code):
    c = int(round(code))
    return table.get(c, f"Unknown({c})")


tap_events, overall_events = {}, {}
for kind, spec in ARRAYS.items():
    spec["paths"] = [path(spec, "BPS_Tap_idx"), path(spec, spec["data"])]
    if kind == "adc":
        fields = [F("adc", T.Float64)]
    else:
        spec["paths"] += [path(spec, spec["fault"]), path(spec, spec["age"])]
        fields = [F(kind, T.Float64, spec["unit"]), F("fault", T.String), F("age", T.Float64, "ms")]
        overall = zelos_sdk.TraceSource(spec["overall"] + params.source_suffix)
        u = spec["unit"]
        overall_events[kind] = overall.add_event("Overall", [
            F(f"min_{kind}", T.Float64, u), F(f"max_{kind}", T.Float64, u), F("spread", T.Float64, u),
            F("min_tap", T.UInt8), F("max_tap", T.UInt8), F("faulted_taps", T.UInt8), F("taps_reporting", T.UInt8)])
    taps = zelos_sdk.TraceSource(spec["taps"] + params.source_suffix)
    tap_events[kind] = {i: taps.add_event(f"Tap_{i:02d}", fields) for i in range(TAPS)}

latest = {kind: {} for kind in ARRAYS}  # kind -> tap -> {"value", "fault"}


def emit(kind, time_ns, row):
    spec = ARRAYS[kind]
    idx = int(round(row[spec["paths"][0]]))
    if not 0 <= idx < TAPS:
        return False
    value = float(row[spec["paths"][1]])
    if kind == "adc":
        tap_events[kind][idx].log_at(time_ns, adc=value)
        return True
    fault_code = int(round(row[spec["paths"][2]])) if not math.isnan(row[spec["paths"][2]]) else 0
    tap_events[kind][idx].log_at(time_ns, **{kind: value}, fault=label(spec["labels"], fault_code),
                                 age=float(row[spec["paths"][3]]))
    if not math.isnan(value):
        latest[kind][idx] = {"value": value, "fault": fault_code}
    seen = latest[kind]
    if seen:
        lo = min(seen, key=lambda i: seen[i]["value"])
        hi = max(seen, key=lambda i: seen[i]["value"])
        overall_events[kind].log_at(time_ns, **{f"min_{kind}": seen[lo]["value"], f"max_{kind}": seen[hi]["value"]},
                                    spread=seen[hi]["value"] - seen[lo]["value"], min_tap=lo, max_tap=hi,
                                    faulted_taps=sum(1 for x in seen.values() if x["fault"] != 0),
                                    taps_reporting=len(seen))
    return True


state = {"last_ns": {k: 0 for k in ARRAYS}, "seen": {k: set() for k in ARRAYS}, "frames": 0, "errors": 0,
         "last_error": None, "worker": None}
stop_event = threading.Event()


def frames(kind, start):
    df = agent.query(ARRAYS[kind]["paths"], start=start).to_pandas().sort_index()
    return df.dropna(subset=[ARRAYS[kind]["paths"][0], ARRAYS[kind]["paths"][1]])


# Seed every tap and both summaries from the recent window, so the panels fill immediately.
seeded = {}
for kind in ARRAYS:
    df = frames(kind, params.window)
    newest = df.groupby(ARRAYS[kind]["paths"][0], sort=True).tail(1).sort_index()
    for ts, row in newest.iterrows():
        emit(kind, int(ts.value), row)
    state["seen"][kind].update((int(ts.value), int(round(r))) for ts, r in df[ARRAYS[kind]["paths"][0]].items())
    if not df.empty:
        state["last_ns"][kind] = int(df.index.max().value)
    seeded[kind] = len(newest)

{"taps_seeded": seeded, "last_source_ns": state["last_ns"]}
```

```python {#5b1d6a02}
def pump_taps():
    while not stop_event.is_set():
        for kind in ARRAYS:
            try:
                last = state["last_ns"][kind]
                start = (datetime.fromtimestamp(max(0, last - 1_000_000_000) / 1e9, tz=timezone.utc)
                         if last else params.window)
                for ts, row in frames(kind, start).iterrows():
                    t = int(ts.value)
                    key = (t, int(round(row[ARRAYS[kind]["paths"][0]])))
                    if key in state["seen"][kind]:
                        continue
                    if emit(kind, t, row):
                        state["frames"] += 1
                    state["seen"][kind].add(key)
                    state["last_ns"][kind] = max(state["last_ns"][kind], t)
                if len(state["seen"][kind]) > 20000:
                    cutoff = state["last_ns"][kind] - 10_000_000_000
                    state["seen"][kind] = {k for k in state["seen"][kind] if k[0] >= cutoff}
                state["last_error"] = None
            except Exception as exc:
                state["errors"] += 1
                state["last_error"] = repr(exc)
        stop_event.wait(params.poll_seconds)


def start_splitter():
    worker = state.get("worker")
    if worker is not None and worker.is_alive():
        return "already running"
    stop_event.clear()
    worker = threading.Thread(target=pump_taps, name="bps-tap-splitter-lilshah", daemon=True)
    state["worker"] = worker
    worker.start()
    return "started"


def stop_splitter():
    stop_event.set()
    worker = state.get("worker")
    if worker is not None:
        worker.join(timeout=3)
    return {"running": bool(worker and worker.is_alive()), "frames_published": state["frames"],
            "errors": state["errors"], "last_error": state["last_error"]}


start_splitter()
```

```python {#5b1d6a03}
{"running": bool(state["worker"] and state["worker"].is_alive()), "frames_published": state["frames"],
 "errors": state["errors"], "last_error": state["last_error"], "poll_seconds": params.poll_seconds,
 "taps_reporting": {k: len(v) for k, v in latest.items() if k != "adc"}}
```
