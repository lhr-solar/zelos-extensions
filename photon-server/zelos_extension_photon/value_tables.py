"""DBC value tables for status signals, so Photon traces show labels ("Closed", "Overtemperature") like
the CAN extension does on live data. Copied from lilShah-finalv2usethis.dbc and SteeringCAN.dbc VAL_ lines."""

from __future__ import annotations

_OPEN_CLOSED = {0: "Open", 1: "Closed"}
_OK_NOT = {0: "NOT OK", 1: "OK"}
_OK_FAULT = {0: "OK", 1: "FAULT"}
_ENABLED = {0: "Disabled", 1: "Enabled"}
_SELECTED = {0: "-", 1: "Selected"}
_PRESSED = {0: "-", 1: "Pressed"}
_OFF_ON = {0: "Off", 1: "On"}

VALUE_TABLES: dict[str, dict[str, dict[int, str]]] = {
    "BPS_Status": {
        "BPS_Fault": {0: "OK", 1: "Overvoltage", 2: "Undervoltage", 3: "Regen", 4: "Overtemperature", 5: "Elcon",
                      6: "Array Precharge Timeout", 7: "Internal Watchdog", 8: "Segment Watchdog",
                      9: "HV Plus Contactor Sense", 10: "HV Minus Contactor Sense", 11: "Array Contactor Sense",
                      12: "Array Pchg Contactor Sense", 13: "Estop 1", 14: "Estop 2", 15: "Estop 3",
                      16: "Charging Overcurrent", 17: "Discharging Overcurrent", 18: "Amperes Watchdog"},
        "BPS_Charge_OK": _OK_NOT,
        "BPS_Regen_OK": _OK_NOT,
        "HV_Plus_Contactor_State": _OPEN_CLOSED,
        "HV_Minus_Contactor_State": _OPEN_CLOSED,
        "Array_Contactor_State": _OPEN_CLOSED,
        "Array_Precharge_Contactor_State": _OPEN_CLOSED,
        **{f"BPS_Segment{i}_Status": _OK_FAULT for i in range(8)},
    },
    # Dash Controls tab: VAL_ 96, 80, 1280, 688, 1664
    "Driver_Input_Status": {
        **dict.fromkeys(("Ignition_Off", "Ignition_Array", "Ignition_Motor", "Gear_Forward", "Gear_Neutral",
                         "Gear_Reverse", "Blinker_Left", "Blinker_Right"), _SELECTED),
        **dict.fromkeys(("Cruise_Set", "PushToTalk_Pressed", "Regen_Activate"), _PRESSED),
        "Hazard_Pressed": _OFF_ON,
        "Horn_Pressed": _OFF_ON,
        "Cruise_Enable": _ENABLED,
        "Regen_Enable": _ENABLED,
    },
    "Pedal_Status": dict.fromkeys(("AccelPedal_Main_Fault", "AccelPedal_Redundant_Fault", "BrakePedal_Main_Fault",
                                   "BrakePedal_Redundant_Fault", "Brake_Pressure_1_Fault", "Brake_Pressure_2_Fault"),
                                  _OK_FAULT),
    "Pump_Status": {"Pump_Fault": _OK_FAULT},
    "LWS_Standard": {
        "LWS_Fault": {0: "OK", 1: "Internal Fault"},
        "LWS_CalibrationStaus": {0: "Sensor Not Calibrated", 1: "Sensor Calibrated"},
        "LWS_Trimming_Status": {0: "FAULT - Sensor Not Trimmed", 1: "Sensor Trimmed"},
    },
    "Display_Status": dict.fromkeys(("Camera_Status_Backup", "Camera_Status_Left", "Camera_Status_Right"), _OK_NOT),
    "BPS_Command": {
        "BPS_Drive_Profile_Enable_Master": _ENABLED,
        "BPS_Regen_Allow": {0: "False", 1: "True"},
        "BPS_Adv_MPPT_Control": _ENABLED,
        "BPS_Soft_Shdn": _ENABLED,
        "BPS_Vsag_Compensation": _ENABLED,
    },
}
