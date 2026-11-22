"""DBC value tables for status signals, so Photon traces show labels ("Closed", "Overtemperature") like
the CAN extension does on live data. Copied from lilShah-finalv2usethis.dbc (VAL_ 1 and VAL_ 103)."""

from __future__ import annotations

_OPEN_CLOSED = {0: "Open", 1: "Closed"}
_OK_NOT = {0: "NOT OK", 1: "OK"}
_OK_FAULT = {0: "OK", 1: "FAULT"}
_ENABLED = {0: "Disabled", 1: "Enabled"}

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
    "BPS_Command": {
        "BPS_Drive_Profile_Enable_Master": _ENABLED,
        "BPS_Regen_Allow": {0: "False", 1: "True"},
        "BPS_Adv_MPPT_Control": _ENABLED,
        "BPS_Soft_Shdn": _ENABLED,
        "BPS_Vsag_Compensation": _ENABLED,
    },
}
