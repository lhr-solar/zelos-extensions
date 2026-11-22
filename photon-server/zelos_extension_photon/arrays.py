"""Split indexed ("array") CAN messages into one signal per channel or tap.

Some messages carry one slot of an array per frame: `PDU_Status_Arr` reports one of 16 high-side
channels (`HSS_Channel_idx`), and the BPS aggregates report one of 32 voltage or temperature taps
(`BPS_Tap_idx`). Plotted as-is they interleave every slot into one line, so they are split per slot,
the way the "PDU Channel Live Splitter LilShah DBC" notebook does for live CAN data:

    PDUChannels_LilShah/Channel_NN        enabled, fault (DBC labels), voltage [V], current [A]
    PDUChannelPower_LilShah/Channel_NN    power [W] = voltage * current
    PDUChannelOverall_LilShah/Overall     total_power [W] of every channel's latest power,
                                          highest_output (channel name), highest_channel_index
    BPSVoltageTaps_LilShah/Tap_NN         voltage [V], fault (DBC label), age [ms]
    BPSVoltageOverall_LilShah/Overall     min/max voltage, spread, min_tap, max_tap, faulted_taps, taps_reporting
    BPSTemperatureTaps_LilShah/Tap_NN     temperature [degC], fault (DBC label), age [ms]
    BPSTemperatureOverall_LilShah/Overall the same summary for temperature
    BPSTempADCTaps_LilShah/Tap_NN         adc (raw thermistor reading)

Photon's 1 s and 100 ms levels average each signal over a bucket, including the index (a 1 s
`HSS_Channel_idx` of 13.5 mixes channels), so these messages are always read raw and then thinned
per slot: the last frame of each slot in each bucket of the requested resolution is kept.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from zelos_sdk import DataType, TraceEventFieldMetadata

from .photon import Row

# Bucket width per Photon level, used to thin raw array frames to the requested resolution.
BUCKET_MS: dict[int, int | None] = {0: None, 1: 100, 2: 1000, 3: 10_000}

PDU_CHANNELS = {
    0: "VCU", 1: "Motor Fans", 2: "Battery", 3: "Moco", 4: "Unassigned_0", 5: "Lights", 6: "Unassigned_1",
    7: "Pedals", 8: "Pump", 9: "MPPTs", 10: "Unassigned_2", 11: "Pi Board", 12: "DEI", 13: "Unassigned_3",
    14: "Telemetry", 15: "PDE Fans",
}  # VAL_ 848 HSS_Channel_idx in lilShah-finalv2usethis.dbc
ENABLED_LABELS = {0: "Disabled", 1: "Enabled"}
PDU_FAULT_LABELS = {0: "OK", 1: "FAULT"}
VOLTAGE_FAULT_LABELS = {0: "OK", 1: "BQ I2C Read Error", 2: "Out-of-Bounds", 3: "Over-Voltage",
                        4: "Under-Voltage", 5: "Message Watchdog"}  # VAL_ 11
TEMPERATURE_FAULT_LABELS = {0: "OK", 1: "Out-of-Bounds (Short to GND)", 2: "Out-of-Bounds (Short to VCC)",
                            3: "Disconnected", 4: "Over-Temperature", 5: "Charge Over-Temperature",
                            6: "Under-Temperature", 7: "Charge Under-Temperature",
                            8: "Message Watchdog"}  # VAL_ 12

F = TraceEventFieldMetadata
Record = tuple[int, str, dict[str, Any]]  # (time_ns, event, fields)


def label(table: dict[int, str], code: float) -> str:
    c = int(round(code))
    return table.get(c, f"Unknown({c})")


@dataclass
class Frame:
    t_ms: int
    k: int  # position among this message's frames at t_ms
    message: str
    values: dict[str, float]


def pair_frames(rows: Iterable[Row], messages: set[str]) -> list[Frame]:
    """Rebuild frames of `messages` from long-format rows (k-th row per signal at a timestamp = k-th frame)."""
    frames: dict[tuple[str, int, int], dict[str, float]] = {}
    occurrence: dict[tuple[str, str, int], int] = {}
    for r in rows:
        if r.message not in messages:
            continue
        k = occurrence.get((r.message, r.signal, r.t_ms), 0)
        occurrence[(r.message, r.signal, r.t_ms)] = k + 1
        frames.setdefault((r.message, r.t_ms, k), {})[r.signal] = r.value
    out = [Frame(t, k, m, v) for (m, t, k), v in frames.items()]
    out.sort(key=lambda f: (f.t_ms, f.k))
    return out


class ArraySplitter:
    """One indexed message: slot naming, the derived sources, and the per-slot state behind Overall."""

    message: str
    index_signal: str
    slots: int
    slot_name: str  # "Channel" or "Tap"

    def __init__(self) -> None:
        self.latest: dict[int, dict[str, Any]] = {}

    def slot_event(self, idx: int) -> str:
        return f"{self.slot_name}_{idx:02d}"

    def sources(self) -> dict[str, dict[str, list[TraceEventFieldMetadata]]]:
        raise NotImplementedError

    def split(self, t_ns: int, idx: int, v: dict[str, float]) -> list[tuple[str, Record]]:
        raise NotImplementedError


class PduSplitter(ArraySplitter):
    """Same sources, fields and math as the "PDU Channel Live Splitter LilShah DBC" notebook."""

    message, index_signal, slots, slot_name = "PDU_Status_Arr", "HSS_Channel_idx", 16, "Channel"
    CHANNELS, POWER, OVERALL = "PDUChannels_LilShah", "PDUChannelPower_LilShah", "PDUChannelOverall_LilShah"

    def sources(self):
        chan = {self.slot_event(i): [F("enabled", DataType.String), F("fault", DataType.String),
                                     F("voltage", DataType.Float64, "V"), F("current", DataType.Float64, "A")]
                for i in range(self.slots)}
        power = {self.slot_event(i): [F("power", DataType.Float64, "W")] for i in range(self.slots)}
        overall = {"Overall": [F("total_power", DataType.Float64, "W"), F("highest_output", DataType.String),
                               F("highest_channel_index", DataType.UInt8)]}
        return {self.CHANNELS: chan, self.POWER: power, self.OVERALL: overall}

    def split(self, t_ns, idx, v):
        if idx not in PDU_CHANNELS:
            return []
        voltage = float(v.get("HSS_Measured_Voltage", math.nan))
        current = float(v.get("HSS_Measured_Current", math.nan))
        power = voltage * current
        ev = self.slot_event(idx)
        out = [(self.CHANNELS, (t_ns, ev, {"enabled": label(ENABLED_LABELS, v.get("HSS_Enabled", -1)),
                                           "fault": label(PDU_FAULT_LABELS, v.get("HSS_Fault", -1)),
                                           "voltage": voltage, "current": current})),
               (self.POWER, (t_ns, ev, {"power": power}))]
        if not math.isnan(power):
            self.latest[idx] = {"power": power}
        if self.latest:
            top = max(self.latest, key=lambda i: self.latest[i]["power"])
            out.append((self.OVERALL, (t_ns, "Overall", {
                "total_power": sum(x["power"] for x in self.latest.values()),
                "highest_output": PDU_CHANNELS[top], "highest_channel_index": top})))
        return out


class _TapSplitter(ArraySplitter):
    index_signal, slots, slot_name = "BPS_Tap_idx", 32, "Tap"
    quantity: str
    unit: str
    data_signal: str
    fault_signal: str
    age_signal: str
    faults: dict[int, str]
    TAPS: str
    OVERALL: str

    def sources(self):
        q, u = self.quantity, self.unit
        taps = {self.slot_event(i): [F(q, DataType.Float64, u), F("fault", DataType.String),
                                     F("age", DataType.Float64, "ms")] for i in range(self.slots)}
        overall = {"Overall": [F(f"min_{q}", DataType.Float64, u), F(f"max_{q}", DataType.Float64, u),
                               F("spread", DataType.Float64, u), F("min_tap", DataType.UInt8),
                               F("max_tap", DataType.UInt8), F("faulted_taps", DataType.UInt8),
                               F("taps_reporting", DataType.UInt8)]}
        return {self.TAPS: taps, self.OVERALL: overall}

    def split(self, t_ns, idx, v):
        if not 0 <= idx < self.slots:
            return []
        value = float(v.get(self.data_signal, math.nan))
        fault_code = int(round(v.get(self.fault_signal, 0)))
        out = [(self.TAPS, (t_ns, self.slot_event(idx), {
            self.quantity: value, "fault": label(self.faults, fault_code),
            "age": float(v.get(self.age_signal, math.nan))}))]
        if not math.isnan(value):
            self.latest[idx] = {"value": value, "fault": fault_code}
        if self.latest:
            lo = min(self.latest, key=lambda i: self.latest[i]["value"])
            hi = max(self.latest, key=lambda i: self.latest[i]["value"])
            q = self.quantity
            out.append((self.OVERALL, (t_ns, "Overall", {
                f"min_{q}": self.latest[lo]["value"], f"max_{q}": self.latest[hi]["value"],
                "spread": self.latest[hi]["value"] - self.latest[lo]["value"], "min_tap": lo, "max_tap": hi,
                "faulted_taps": sum(1 for x in self.latest.values() if x["fault"] != 0),
                "taps_reporting": len(self.latest)})))
        return out


class BpsVoltageSplitter(_TapSplitter):
    message, quantity, unit = "BPS_Voltage_Aggregate_Arr", "voltage", "V"
    data_signal, fault_signal, age_signal = "BPS_Voltage_Tap_Data", "BPS_Voltage_Tap_Fault", "BPS_Voltage_Tap_Age"
    faults = VOLTAGE_FAULT_LABELS
    TAPS, OVERALL = "BPSVoltageTaps_LilShah", "BPSVoltageOverall_LilShah"


class BpsTemperatureSplitter(_TapSplitter):
    message, quantity, unit = "BPS_Temperature_Aggregate_Arr", "temperature", "°C"
    data_signal = "BPS_Temperature_Tap_Data"
    fault_signal, age_signal = "BPS_Temperature_Tap_Fault", "BPS_Temperature_Tap_Age"
    faults = TEMPERATURE_FAULT_LABELS
    TAPS, OVERALL = "BPSTemperatureTaps_LilShah", "BPSTemperatureOverall_LilShah"


class BpsTempAdcSplitter(ArraySplitter):
    message, index_signal, slots, slot_name = "BPS_Temp_ADC_Aggregate_Arr", "BPS_Tap_idx", 32, "Tap"
    TAPS = "BPSTempADCTaps_LilShah"

    def sources(self):
        return {self.TAPS: {self.slot_event(i): [F("adc", DataType.Float64)] for i in range(self.slots)}}

    def split(self, t_ns, idx, v):
        if not 0 <= idx < self.slots:
            return []
        return [(self.TAPS, (t_ns, self.slot_event(idx),
                             {"adc": float(v.get("BPS_Temperature_Tap_ADC", math.nan))}))]


SPLITTERS: dict[str, type[ArraySplitter]] = {
    s.message: s for s in (PduSplitter, BpsVoltageSplitter, BpsTemperatureSplitter, BpsTempAdcSplitter)}
ARRAY_MESSAGES = frozenset(SPLITTERS)


@dataclass
class ArrayRouter:
    """Turns raw frames of every array message present into thinned array records plus split sources.

    `derive=False` only thins (live-workspace loads, where the live notebooks own the split sources).
    State (each slot's latest value, buckets already used) carries across chunks.
    """

    present: set[str]
    level: int
    derive: bool = True
    splitters: dict[str, ArraySplitter] = field(default_factory=dict)
    signals: dict[str, list[str]] = field(default_factory=dict)  # Photon signal names per array message
    _kept: dict[tuple[str, int], int] = field(default_factory=dict)  # (message, slot) -> last bucket written

    def __post_init__(self) -> None:
        self.present = set(self.present) & ARRAY_MESSAGES
        self.splitters = {m: SPLITTERS[m]() for m in self.present}

    def derived_sources(self) -> dict[str, dict[str, list[TraceEventFieldMetadata]]]:
        out: dict[str, dict[str, list[TraceEventFieldMetadata]]] = {}
        if self.derive:
            for sp in self.splitters.values():
                for src, events in sp.sources().items():
                    out.setdefault(src, {}).update(events)
        return out

    def thin(self, frames: list[Frame]) -> list[Frame]:
        """Keep the last frame of each slot in each bucket (all frames at raw)."""
        bucket = BUCKET_MS.get(self.level)
        if not bucket:
            return frames
        last: dict[tuple[str, int, int], Frame] = {}
        for f in frames:
            sp = self.splitters.get(f.message)
            if sp is None or sp.index_signal not in f.values:
                continue
            idx = int(round(f.values[sp.index_signal]))
            last[(f.message, idx, f.t_ms // bucket)] = f
        kept = []
        for (m, idx, b), f in last.items():
            if self._kept.get((m, idx), -1) >= b:  # already written from the previous chunk
                continue
            self._kept[(m, idx)] = b
            kept.append(f)
        kept.sort(key=lambda f: (f.t_ms, f.k))
        return kept

    def route(self, frames: list[Frame]) -> tuple[list[tuple[str, dict[str, float], int]], dict[str, list[Record]]]:
        """Returns (array frames to log as CAN messages: (message, values, t_ms), derived records per source)."""
        kept = self.thin(frames)
        derived: dict[str, list[Record]] = {}
        raw_out = []
        for f in kept:
            sp = self.splitters.get(f.message)
            if sp is None or sp.index_signal not in f.values:
                continue
            raw_out.append((f.message, f.values, f.t_ms))
            if self.derive:
                idx = int(round(f.values[sp.index_signal]))
                for src, rec in sp.split(f.t_ms * 1_000_000, idx, f.values):
                    derived.setdefault(src, []).append(rec)
        return raw_out, derived
