"""Add (or refresh) a "Battery" tab in a shared Zelos layout: pack status, cell voltage/temperature
summaries, contactor and segment states, min/max trends and all 32 taps.

    python tools/add_battery_tab.py                 # layout "High Noon"
    python tools/add_battery_tab.py --layout "My Layout" --dry-run

One tab serves both views because it binds to the same names in both:
- live: the CAN extension's `CAN/CarCAN/...` signals plus the sources the
  "BPS Tap Live Splitter LilShah DBC" notebook writes while it runs;
- historical: any Photon trace, which the Photon extension writes with the same CAN names and the
  same `BPS*_LilShah` split sources.

Re-running replaces only the tab named "Battery"; other tabs are untouched. Each run is a new layout
version (`agent.layouts.restore(id, version)` undoes it).
"""

from __future__ import annotations

import argparse
import itertools
import json
import uuid

import zelos_sdk

TAB_NAME = "Battery"
_NS = uuid.UUID("0b7c5e1d-8a24-4f3e-9d61-2c4b7a9e3f10")
_COLORS = itertools.cycle(["#5794F2", "#FA6400", "#22A15E", "#B877D9", "#F2CC0C", "#1A8993", "#E02F44", "#A87124"])
CAN_EVENT_TYPE = "zelos.can.message.v1"


def _id(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def can(message: str, signal: str) -> dict:
    return {"color": next(_COLORS), "eventType": CAN_EVENT_TYPE, "message": f"CarCAN/{message}", "signal": signal,
            "source": "CAN"}


def derived(source: str, event: str, field: str) -> dict:
    return {"color": next(_COLORS), "message": event, "signal": field, "source": source}


def loc(x: float, y: float, w: float, h: float) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


def value(key: str, title: str, location: dict, signals: list[dict], description: str, columns: int) -> dict:
    return {"id": _id(key), "kind": "VALUE", "title": title, "location": location, "signal": signals[0],
            "signals": signals, "options": {"columnCount": columns, "description": description, "fontSize": 22,
                                            "showEmptySignals": True, "showFullSignalName": True}}


def state(key: str, title: str, location: dict, signals: list[dict]) -> dict:
    return {"id": _id(key), "kind": "STATE", "title": title, "location": location, "signals": signals,
            "options": {"alignStackedAxes": True, "fontSize": 8, "hideXAxis": False, "showEmptySignals": True,
                        "showFullSignalName": True, "showLiveValues": True,
                        "legend": {"placement": "left", "sizePercent": {"left": 22}, "visible": True}}}


def plot(key: str, title: str, location: dict, signals: list[dict]) -> dict:
    return {"id": _id(key), "kind": "PLOT", "title": title, "location": location, "signals": signals,
            "options": {"alignStackedAxes": True, "fillArea": False, "hideXAxis": False, "hideYAxis": False,
                        "legend": {"placement": "bottom", "visible": True}, "linePathMode": "linear",
                        "linePattern": "solid", "lineWidth": 1, "markerSize": 3, "markerVisibility": "auto",
                        "showEmptySignals": True, "showFullSignalName": True, "showLiveValues": True,
                        "yAxisLayout": "shared", "yAxisNumberFormat": "auto"}}


def table(key: str, title: str, location: dict, signals: list[dict]) -> dict:
    return {"id": _id(key), "kind": "TABLE", "title": title, "location": location, "signals": signals,
            "options": {"showEmptySignals": True, "showFullSignalName": True}}


def battery_tab() -> dict:
    vo, to = "BPSVoltageOverall_LilShah", "BPSTemperatureOverall_LilShah"
    taps = range(32)
    panels = [
        value("pack", "Pack", loc(0, 0, 12, 0.7), [
            can("BPS_Status", "Main_Battery_Voltage"), can("BPS_Status", "Main_Battery_Avg_Temperature"),
            can("BPS_Precharge_Voltages", "BPS_Precharge_Battery_Voltage"),
            can("BPS_Precharge_Voltages", "BPS_Precharge_Array_Voltage")],
            "Pack voltage and average temperature (BPS_Status), and the precharge voltages.", 4),
        value("cellv", "Cell voltage", loc(0, 0.7, 12, 0.7), [
            derived(vo, "Overall", f) for f in ("min_voltage", "max_voltage", "spread", "min_tap", "max_tap",
                                                "faulted_taps", "taps_reporting")],
            "Lowest and highest cell (tap) voltage across the 32 taps, the spread, which taps, and how many taps "
            "report a fault.", 7),
        value("cellt", "Cell temperature", loc(0, 1.4, 12, 0.7), [
            derived(to, "Overall", f) for f in ("min_temperature", "max_temperature", "spread", "min_tap",
                                                "max_tap", "faulted_taps", "taps_reporting")],
            "Lowest and highest tap temperature, the spread, which taps, and how many taps report a fault.", 7),
        state("status", "BPS status", loc(0, 2.1, 6, 1.3), [
            can("BPS_Status", s) for s in ("BPS_Fault", "BPS_Charge_OK", "BPS_Regen_OK", "HV_Plus_Contactor_State",
                                           "HV_Minus_Contactor_State", "Array_Contactor_State",
                                           "Array_Precharge_Contactor_State")]),
        state("segments", "Segment status", loc(6, 2.1, 6, 1.3), [
            can("BPS_Status", f"BPS_Segment{i}_Status") for i in range(8)]),
        plot("vtrend", "Cell voltage range", loc(0, 3.4, 6, 1.1), [
            derived(vo, "Overall", "min_voltage"), derived(vo, "Overall", "max_voltage")]),
        plot("ttrend", "Cell temperature range", loc(6, 3.4, 6, 1.1), [
            derived(to, "Overall", "min_temperature"), derived(to, "Overall", "max_temperature"),
            can("BPS_Status", "Main_Battery_Avg_Temperature")]),
        table("vtaps", "Tap voltages", loc(0, 4.5, 6, 2.6), [
            derived("BPSVoltageTaps_LilShah", f"Tap_{i:02d}", "voltage") for i in taps]),
        table("ttaps", "Tap temperatures", loc(6, 4.5, 6, 2.6), [
            derived("BPSTemperatureTaps_LilShah", f"Tap_{i:02d}", "temperature") for i in taps]),
        table("faults", "Tap faults", loc(0, 7.1, 12, 2.6), [
            *(derived("BPSVoltageTaps_LilShah", f"Tap_{i:02d}", "fault") for i in taps),
            *(derived("BPSTemperatureTaps_LilShah", f"Tap_{i:02d}", "fault") for i in taps)]),
    ]
    return {"id": _id("tab"), "kind": "LAYOUT", "name": TAB_NAME, "layout": {"panels": panels}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="High Noon")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    tab = battery_tab()
    if args.dry_run:
        print(json.dumps(tab, indent=1))
        return
    agent = zelos_sdk.connect()
    match = [lay for lay in agent.layouts.list() if lay.name == args.layout]
    if len(match) != 1:
        raise SystemExit(f"expected one layout named {args.layout!r}, found {len(match)}")
    layout = agent.layouts.show(match[0].id)
    data = layout.data if isinstance(layout.data, dict) else json.loads(layout.data)
    tabs = [t for t in data.get("tabs", []) if t.get("name") != TAB_NAME]
    data = {**data, "tabs": [*tabs, tab]}
    agent.layouts.update(layout.id, data=data)
    print(f"{args.layout}: tabs now {[t['name'] for t in data['tabs']]}")


if __name__ == "__main__":
    main()
