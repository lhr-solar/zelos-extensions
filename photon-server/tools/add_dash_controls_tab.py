"""Fill the "Dash Controls" tab in a shared Zelos layout with Photon's Dashboard debug page (press D on
Dashboard): driver switches, pedals and brakes with their fault flags, steering, and cooling.

    python tools/add_dash_controls_tab.py                 # layout "High Noon"
    python tools/add_dash_controls_tab.py --layout "My Layout" --dry-run

Signals bind to the CAN extension's names (`CAN/CarCAN/<Message>`), which Photon traces use too; status
fields carry the DBC labels ("Selected", "On", "FAULT") in both. Photon does not store `LWS_Standard`
(steering) or `Coolant_Temperature`, so those fill in live only.

Re-running replaces only the tab named "Dash Controls" (keeping its id and place); other tabs are
untouched. Each run is a new layout version (`agent.layouts.restore(id, version)` undoes it).
"""

from __future__ import annotations

import argparse
import json
import uuid

import zelos_sdk
from add_battery_tab import can, loc, plot, state, value

TAB_NAME = "Dash Controls"
_NS = uuid.UUID("a3e1c7b4-2f59-4d08-8b6e-0c9d5f1a7e42")

SWITCHES = ["Ignition_Off", "Ignition_Array", "Ignition_Motor", "Gear_Forward", "Gear_Neutral", "Gear_Reverse",
            "Blinker_Left", "Blinker_Right", "Hazard_Pressed", "Horn_Pressed", "PushToTalk_Pressed",
            "Cruise_Enable", "Cruise_Set", "Regen_Enable", "Regen_Activate"]
PEDAL_FAULTS = ["AccelPedal_Main_Fault", "AccelPedal_Redundant_Fault", "BrakePedal_Main_Fault",
                "BrakePedal_Redundant_Fault", "Brake_Pressure_1_Fault", "Brake_Pressure_2_Fault"]
PEDALS = ["AccelPedal_Main_Pos", "AccelPedal_Redundant_Pos", "BrakePedal_Main_Pos", "BrakePedal_Redundant_Pos"]


def k(name: str) -> str:
    return f"dash-controls/{name}"  # panel ids are uuid5(key): keep them apart from other tabs'


def dash_controls_tab(tab_id: str) -> dict:
    panels = [
        value(k("now"), "Pedals, brakes and steering", loc(0, 0, 12, 0.7), [
            *(can("Pedal_Status", s) for s in PEDALS), can("Brake_Pressure_1", "Brake_Pressure"),
            can("Brake_Pressure_2", "Brake_Pressure"), can("LWS_Standard", "LWS_Angle")],
            "Accelerator and brake pedal position (main and redundant sensor), front (1) and rear (2) brake "
            "pressure, and steering angle. Steering is live only: Photon does not store LWS_Standard.", 7),
        state(k("switches"), "Driver switches", loc(0, 0.7, 6, 2.3),
              [can("Driver_Input_Status", s) for s in SWITCHES]),
        state(k("faults"), "Faults and sensor status", loc(6, 0.7, 6, 1.5), [
            *(can("Pedal_Status", s) for s in PEDAL_FAULTS), can("Pump_Status", "Pump_Fault"),
            *(can("LWS_Standard", s) for s in ("LWS_Fault", "LWS_CalibrationStaus", "LWS_Trimming_Status")),
            *(can("Display_Status", f"Camera_Status_{c}") for c in ("Backup", "Left", "Right"))]),
        plot(k("pedals"), "Pedal position (%): main vs redundant", loc(6, 2.2, 6, 0.8),
             [can("Pedal_Status", s) for s in PEDALS]),
        value(k("cooling"), "Cooling", loc(0, 3.0, 12, 0.7), [
            can("Coolant_Temperature", "Coolant_Temperature_1"), can("Coolant_Temperature", "Coolant_Temperature_2"),
            can("Pump_Status", "FlowRate_1"), can("Pump_Status", "FlowRate_2"), can("Pump_Status", "Pump_DutyCycle"),
            can("MC_Motor_TempMeasurement", "MC_MotorTemp"), can("MC_Motor_TempMeasurement", "MC_HeatsinkTemp")],
            "Coolant temperature (live only: Photon does not store Coolant_Temperature), pump flow and duty, and "
            "the motor and heatsink temperatures they cool.", 7),
        plot(k("temps"), "Coolant, motor and heatsink temperature (°C)", loc(0, 3.7, 6, 1.1), [
            can("Coolant_Temperature", "Coolant_Temperature_1"), can("Coolant_Temperature", "Coolant_Temperature_2"),
            can("MC_Motor_TempMeasurement", "MC_MotorTemp"), can("MC_Motor_TempMeasurement", "MC_HeatsinkTemp")]),
        plot(k("pump"), "Pump flow (L/min) and duty (%)", loc(6, 3.7, 6, 1.1), [
            can("Pump_Status", "FlowRate_1"), can("Pump_Status", "FlowRate_2"), can("Pump_Status", "Pump_DutyCycle")]),
        plot(k("brakes"), "Brake pedal (%) vs brake pressure (psi)", loc(0, 4.8, 12, 1.1), [
            can("Pedal_Status", "BrakePedal_Main_Pos"), can("Brake_Pressure_1", "Brake_Pressure"),
            can("Brake_Pressure_2", "Brake_Pressure")]),
    ]
    return {"id": tab_id, "kind": "LAYOUT", "name": TAB_NAME, "layout": {"panels": panels}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="High Noon")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print(json.dumps(dash_controls_tab(str(uuid.uuid5(_NS, "tab"))), indent=1))
        return
    agent = zelos_sdk.connect()
    match = [lay for lay in agent.layouts.list() if lay.name == args.layout]
    if len(match) != 1:
        raise SystemExit(f"expected one layout named {args.layout!r}, found {len(match)}")
    layout = agent.layouts.show(match[0].id)
    data = layout.data if isinstance(layout.data, dict) else json.loads(layout.data)
    tabs = list(data.get("tabs", []))
    at = next((i for i, t in enumerate(tabs) if t.get("name") == TAB_NAME), None)
    if at is None:
        tabs.append(dash_controls_tab(str(uuid.uuid5(_NS, "tab"))))
    else:
        tabs[at] = dash_controls_tab(tabs[at].get("id") or str(uuid.uuid5(_NS, "tab")))
    agent.layouts.update(layout.id, data={**data, "tabs": tabs})
    print(f"{args.layout}: tabs now {[t['name'] for t in tabs]}")


if __name__ == "__main__":
    main()
