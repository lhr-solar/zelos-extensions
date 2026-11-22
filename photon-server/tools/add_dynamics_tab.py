"""Fill the "Dynamics" tab in a shared Zelos layout with Photon's Mechanical/Dynamics views: body and corner
IMUs, yaw rate, speeds in mph, driver input, suspension travel and wheel rpm.

    python tools/add_dynamics_tab.py                 # layout "High Noon"
    python tools/add_dynamics_tab.py --layout "My Layout" --dry-run

Signals bind to the CAN extension's names (`CAN/CarCAN/<Message>`), which Photon traces use too, plus
`Speed_LilShah/Speed` (wheel and vehicle mph), which the Photon extension computes for history and the
"Speed Live LilShah DBC" notebook computes live. Live decoding of the IMU, suspension and wheel messages
needs `dbc/VCAT-Sensors.dbc` in the CAN extension's database files (none of the Sharepoint DBCs define them).

Re-running replaces only the tab named "Dynamics" (keeping its id); other tabs are untouched. Each run is a
new layout version (`agent.layouts.restore(id, version)` undoes it).
"""

from __future__ import annotations

import argparse
import json
import uuid

import zelos_sdk
from add_battery_tab import can, derived, loc, plot, table, value

TAB_NAME = "Dynamics"
_NS = uuid.UUID("5d0f3c2a-7e41-4b8c-a3f6-91e2d4b7c058")
SPEED = "Speed_LilShah"


def k(name: str) -> str:
    return f"dynamics/{name}"  # panel ids are uuid5(key): keep them apart from the Battery tab's


def accel(axis: str) -> list[dict]:
    return [can(m, f"Accel_{axis}") for m in ("IMU_B_Accel", "IMU_Accel_FL", "IMU_Accel_FR")]


def dynamics_tab(tab_id: str) -> dict:
    corners = ("Suspension_FL", "Suspension_FR", "Suspension_Back")
    panels = [
        value(k("now"), "Now", loc(0, 0, 12, 0.7), [
            derived(SPEED, "Speed", "vehicle"), derived(SPEED, "Speed", "wheel_FL"),
            derived(SPEED, "Speed", "wheel_FR"), *(can("IMU_B_Accel", f"Accel_{a}") for a in "XYZ"),
            can("IMU_Gyro_Back", "Gyro_Z"), can("Pedal_Status", "AccelPedal_Main_Pos"),
            can("LWS_Standard", "LWS_Angle")],
            "Vehicle speed (motor controller) and wheel speed in mph, body acceleration per axis, body yaw "
            "rate, throttle and steering angle. Steering is live only: Photon does not store LWS_Standard.", 9),
        # Photon draws Accel_X as longitudinal and Accel_Y as lateral, but X carries gravity (body reads about
        # -1 g while driving), so the axes are shown by name until the IMU mounting is confirmed.
        plot(k("ax"), "Accel X (g): body, FL, FR (reads gravity)", loc(0, 0.7, 4, 1.2), accel("X")),
        plot(k("ay"), "Accel Y (g): body, FL, FR", loc(4, 0.7, 4, 1.2), accel("Y")),
        plot(k("az"), "Accel Z (g): body, FL, FR", loc(8, 0.7, 4, 1.2), accel("Z")),
        plot(k("speed"), "Speed (mph)", loc(0, 1.9, 6, 1.2), [
            derived(SPEED, "Speed", f) for f in ("vehicle", "wheel_FL", "wheel_FR")]),
        plot(k("yaw"), "Body yaw rate (deg/s)", loc(6, 1.9, 6, 1.2), [can("IMU_Gyro_Back", "Gyro_Z")]),
        plot(k("driver"), "Driver input: throttle %, brake psi, steering deg", loc(0, 3.1, 6, 1.2), [
            can("Pedal_Status", "AccelPedal_Main_Pos"), can("Brake_Pressure_1", "Brake_Pressure"),
            can("Brake_Pressure_2", "Brake_Pressure"), can("LWS_Standard", "LWS_Angle")]),
        plot(k("susp"), "Suspension travel (SUS_Data1, raw counts)", loc(6, 3.1, 6, 1.2),
             [can(m, "SUS_Data1") for m in corners]),
        plot(k("rpm"), "Wheel rpm", loc(0, 4.3, 6, 1.2), [can("WheelRPM_FL", "RPM"), can("WheelRPM_FR", "RPM")]),
        plot(k("gyro"), "Gyro Z (deg/s): body vs front (front uncalibrated)", loc(6, 4.3, 6, 1.2), [
            can("IMU_Gyro_Back", "Gyro_Z"), can("IMU_Gyro_FL", "Gyro_Z"), can("IMU_Gyro_FR", "Gyro_Z")]),
        table(k("suspall"), "Suspension channels (raw counts)", loc(0, 5.5, 6, 1.6), [
            can(m, f"SUS_Data{i}") for m in corners for i in range(1, 5)]),
        value(k("imutemp"), "IMU temperatures", loc(6, 5.5, 6, 0.7), [
            can(m, "Temperature") for m in ("B_IMU_Temp", "FL_IMU_Temp", "FR_IMU_Temp")],
            "IMU die temperatures (body, front-left, front-right).", 3),
    ]
    return {"id": tab_id, "kind": "LAYOUT", "name": TAB_NAME, "layout": {"panels": panels}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="High Noon")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print(json.dumps(dynamics_tab(str(uuid.uuid5(_NS, "tab"))), indent=1))
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
        tabs.append(dynamics_tab(str(uuid.uuid5(_NS, "tab"))))
    else:
        tabs[at] = dynamics_tab(tabs[at].get("id") or str(uuid.uuid5(_NS, "tab")))  # same place in the tab bar
    agent.layouts.update(layout.id, data={**data, "tabs": tabs})
    print(f"{args.layout}: tabs now {[t['name'] for t in tabs]}")


if __name__ == "__main__":
    main()
