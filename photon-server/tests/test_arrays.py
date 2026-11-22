import math

import pytest
from conftest import FakeClient, FakeSource

from zelos_extension_photon.arrays import (
    ArrayRouter,
    BpsTemperatureSplitter,
    BpsVoltageSplitter,
    PduSplitter,
    pair_frames,
)
from zelos_extension_photon.loader import (
    LiveLoader,
    Progress,
    allowed_fields,
    can_naming,
    declare,
    group_rows,
    make_router,
    pump,
    write_trace,
)
from zelos_extension_photon.photon import CatalogSession, Row
from zelos_extension_photon.sessions import Window

PDU = ["HSS_Channel_idx", "HSS_Enabled", "HSS_Fault", "HSS_Measured_Voltage", "HSS_Measured_Current"]
S = CatalogSession("20260927-211337-00", 0, 10_000, 0, False, "")


def pdu_rows(frames: list[tuple[int, int, float, float]], enabled: int = 1, fault: int = 0) -> list[Row]:
    """Long-format rows (grouped by signal, like /export.csv) for (t_ms, channel, volts, amps) frames."""
    cols = {"HSS_Channel_idx": [float(c) for _, c, _, _ in frames], "HSS_Enabled": [float(enabled)] * len(frames),
            "HSS_Fault": [float(fault)] * len(frames), "HSS_Measured_Voltage": [v for _, _, v, _ in frames],
            "HSS_Measured_Current": [a for _, _, _, a in frames]}
    return [Row(t, "PDU_Status_Arr", sig, "", cols[sig][i]) for sig in PDU for i, (t, *_rest) in enumerate(frames)]


def test_frames_sharing_a_timestamp_are_paired_in_order():
    # three PDU frames in one CANP batch (same timestamp), as Photon stores them
    rows = pdu_rows([(1000, 3, 24.0, 0.5), (1000, 4, 24.1, 0.0), (1000, 5, 24.2, 1.0)])
    frames = pair_frames(rows, {"PDU_Status_Arr"})
    assert [(f.values["HSS_Channel_idx"], f.values["HSS_Measured_Current"]) for f in frames] == [
        (3.0, 0.5), (4.0, 0.0), (5.0, 1.0)]
    # the general path keeps all three too (it used to keep only the last)
    recs, seen, _ = group_rows(rows)
    assert seen == 15 and [r[2]["HSS_Channel_idx"] for r in recs] == [3.0, 4.0, 5.0]


def test_pdu_split_matches_the_notebook():
    sp = PduSplitter()
    out = sp.split(5_000_000, 2, {"HSS_Channel_idx": 2, "HSS_Enabled": 1, "HSS_Fault": 0,
                                  "HSS_Measured_Voltage": 24.0, "HSS_Measured_Current": 1.5})
    by_src = dict(out)
    assert by_src["PDUChannels_LilShah"] == (5_000_000, "Channel_02", {"enabled": "Enabled", "fault": "OK",
                                                                       "voltage": 24.0, "current": 1.5})
    assert by_src["PDUChannelPower_LilShah"] == (5_000_000, "Channel_02", {"power": 36.0})
    assert by_src["PDUChannelOverall_LilShah"][2] == {"total_power": 36.0, "highest_output": "Battery",
                                                      "highest_channel_index": 2}
    out = sp.split(6_000_000, 14, {"HSS_Channel_idx": 14, "HSS_Enabled": 0, "HSS_Fault": 1,
                                   "HSS_Measured_Voltage": 24.0, "HSS_Measured_Current": 2.0})
    by_src = dict(out)
    assert by_src["PDUChannels_LilShah"][2]["enabled"] == "Disabled"
    assert by_src["PDUChannels_LilShah"][2]["fault"] == "FAULT"
    # total is every channel's latest power; highest is Telemetry (48 W) over Battery (36 W)
    assert by_src["PDUChannelOverall_LilShah"][2] == {"total_power": 84.0, "highest_output": "Telemetry",
                                                      "highest_channel_index": 14}
    assert {e: [f.name for f in fs] for e, fs in sp.sources()["PDUChannels_LilShah"].items()}["Channel_15"] == [
        "enabled", "fault", "voltage", "current"]


def test_unknown_codes_get_a_readable_label():
    out = dict(PduSplitter().split(1, 0, {"HSS_Enabled": 3, "HSS_Fault": 7, "HSS_Measured_Voltage": 1,
                                          "HSS_Measured_Current": 1}))
    assert out["PDUChannels_LilShah"][2]["enabled"] == "Unknown(3)"
    assert out["PDUChannels_LilShah"][2]["fault"] == "Unknown(7)"


def test_tap_overall_tracks_min_max_and_faults():
    sp = BpsVoltageSplitter()
    sp.split(1, 0, {"BPS_Voltage_Tap_Data": 3.60, "BPS_Voltage_Tap_Fault": 0, "BPS_Voltage_Tap_Age": 10})
    sp.split(2, 7, {"BPS_Voltage_Tap_Data": 3.20, "BPS_Voltage_Tap_Fault": 4, "BPS_Voltage_Tap_Age": 12})
    out = dict(sp.split(3, 31, {"BPS_Voltage_Tap_Data": 4.10, "BPS_Voltage_Tap_Fault": 0,
                                "BPS_Voltage_Tap_Age": 9}))
    tap = out["BPSVoltageTaps_LilShah"]
    assert tap == (3, "Tap_31", {"voltage": 4.10, "fault": "OK", "age": 9.0})
    ov = out["BPSVoltageOverall_LilShah"][2]
    assert (ov["min_voltage"], ov["max_voltage"], ov["min_tap"], ov["max_tap"]) == (3.20, 4.10, 7, 31)
    assert math.isclose(ov["spread"], 0.90) and ov["faulted_taps"] == 1 and ov["taps_reporting"] == 3
    t = dict(BpsTemperatureSplitter().split(1, 5, {"BPS_Temperature_Tap_Data": 31.5,
                                                   "BPS_Temperature_Tap_Fault": 3, "BPS_Temperature_Tap_Age": 4}))
    assert t["BPSTemperatureTaps_LilShah"][2]["fault"] == "Disconnected"
    assert t["BPSTemperatureOverall_LilShah"][2]["max_temperature"] == 31.5


def test_thinning_keeps_each_channels_last_frame_per_bucket_and_never_averages():
    frames = pair_frames(pdu_rows([(100, 0, 24.0, 1.0), (400, 1, 24.0, 2.0), (900, 0, 24.0, 3.0),
                                   (1200, 0, 24.0, 4.0), (1300, 1, 24.0, 5.0)]), {"PDU_Status_Arr"})
    router = ArrayRouter(present={"PDU_Status_Arr"}, level=2)  # 1 s buckets
    raw_out, derived = router.route(frames)
    kept = [(t, v["HSS_Channel_idx"], v["HSS_Measured_Current"]) for _, v, t in raw_out]
    assert kept == [(400, 1.0, 2.0), (900, 0.0, 3.0), (1200, 0.0, 4.0), (1300, 1.0, 5.0)]
    assert all(float(i).is_integer() for _, i, _ in kept)
    assert len(derived["PDUChannels_LilShah"]) == 4
    # a bucket already written in an earlier chunk is not written again
    again, _ = router.route(pair_frames(pdu_rows([(1400, 0, 24.0, 9.0)]), {"PDU_Status_Arr"}))
    assert again == []


def averaged_pdu_rows() -> list[Row]:
    """What Photon's 1 s level returns for PDU_Status_Arr: every signal averaged, index included."""
    return [Row(1000, "PDU_Status_Arr", sig, "", 7.58) for sig in PDU]


def other_rows(level_step: int) -> list[Row]:
    return [Row(t, "Pedal_Status", "AccelPedal_Main_Pos", "%", 10.0) for t in range(0, 4000, level_step)]


def test_pump_reads_arrays_raw_and_splits_them():
    raw = pdu_rows([(500, 2, 24.0, 1.0), (500, 3, 24.0, 2.0), (2500, 2, 24.0, 1.5)])
    client = FakeClient({2: averaged_pdu_rows() + other_rows(1000), 0: raw + other_rows(10)})
    schema = {"PDU_Status_Arr": dict.fromkeys(PDU, ""), "Pedal_Status": {"AccelPedal_Main_Pos": "%"}}
    naming = can_naming()
    main, derived_src = FakeSource(), {name: FakeSource() for name in
                                       ("PDUChannels_LilShah", "PDUChannelPower_LilShah", "PDUChannelOverall_LilShah")}
    declare(main, schema, naming=naming)
    totals = pump(client, S, Window(0, 4000), 2, None, main, allowed_fields(schema, naming), Progress(), naming,
                  router=make_router(schema, 2, True), derived_sources=derived_src)
    pdu = [r for r in main.records if r[1] == "CarCAN/PDU_Status_Arr"]
    assert [r[2]["HSS_Channel_idx"] for r in pdu] == [2.0, 3.0, 2.0]  # raw frames, the 7.58 average is gone
    assert all(r[2]["HSS_Channel_idx"] != 7.58 for r in pdu)
    assert len([r for r in main.records if r[1] == "CarCAN/Pedal_Status"]) == 4  # other messages stay at 1 s
    assert [r[1] for r in derived_src["PDUChannels_LilShah"].records] == ["Channel_02", "Channel_03", "Channel_02"]
    assert derived_src["PDUChannelOverall_LilShah"].records[-1][2]["total_power"] == 24.0 * 1.5 + 24.0 * 2.0
    assert totals["array_frames"] == 3 and totals["split_records"] == 9
    raw_fetches = [c for c in client.calls if c[3] == 0]
    assert raw_fetches and all(sig.startswith("PDU_Status_Arr.") for sig in raw_fetches[0][4])


def test_raw_loads_take_arrays_from_the_main_stream():
    raw = pdu_rows([(500, 2, 24.0, 1.0), (500, 3, 24.0, 2.0)])
    client = FakeClient({0: raw + other_rows(1000)})
    schema = {"PDU_Status_Arr": dict.fromkeys(PDU, ""), "Pedal_Status": {"AccelPedal_Main_Pos": "%"}}
    main = FakeSource()
    declare(main, schema, naming=can_naming())
    pump(client, S, Window(0, 4000), 0, None, main, allowed_fields(schema, can_naming()), Progress(), can_naming(),
         router=make_router(schema, 0, True), derived_sources={})
    assert len(client.calls) == 1  # no second fetch at raw
    assert [r[2]["HSS_Channel_idx"] for r in main.records if "PDU" in r[1]] == [2.0, 3.0]


def test_write_trace_adds_the_split_sources(tmp_path):
    raw = pdu_rows([(500, 2, 24.0, 1.0)])
    client = FakeClient({5: averaged_pdu_rows() + other_rows(1000), 2: averaged_pdu_rows() + other_rows(1000),
                         0: raw})
    out = write_trace(client, S, Window(0, 4000), 2, None, tmp_path / "t.trz", Progress())
    assert out["split_sources"] == ["PDUChannelOverall_LilShah", "PDUChannelPower_LilShah", "PDUChannels_LilShah"]
    assert out["array_frames"] == 1 and (tmp_path / "t.trz").stat().st_size > 1000


def test_live_loads_only_thin_arrays():
    raw = pdu_rows([(500, 2, 24.0, 1.0)])
    client = FakeClient({5: averaged_pdu_rows(), 2: averaged_pdu_rows(), 0: raw})
    made: dict[str, FakeSource] = {}
    res = LiveLoader(lambda: client, lambda name: made.setdefault(name, FakeSource())).load(
        S, Window(0, 4000), 2, None, Progress())
    (src,) = made.values()
    assert [r[2]["HSS_Channel_idx"] for r in src.records] == [2.0]
    assert res["split_records"] == 0


@pytest.mark.parametrize("level", [1, 2, 3])
def test_array_messages_are_never_logged_from_averaged_levels(level):
    client = FakeClient({level: averaged_pdu_rows(), 0: []})
    schema = {"PDU_Status_Arr": dict.fromkeys(PDU, "")}
    main = FakeSource()
    declare(main, schema, naming=can_naming())
    pump(client, S, Window(0, 4000), level, None, main, allowed_fields(schema, can_naming()), Progress(),
         can_naming(), router=make_router(schema, level, True), derived_sources={})
    assert main.records == []


MPPT = ["MPPT_Input_Voltage", "MPPT_Input_Current", "MPPT_Output_Voltage", "MPPT_Output_Current"]


def mppt_rows(unit: str, frames: list[tuple[int, float, float, float, float]]) -> list[Row]:
    """(t_ms, in_V, in_A, out_V, out_A) frames as long-format rows."""
    msg = f"MPPT_{unit}_PowerMeasurements"
    return [Row(t, msg, sig, "", vals[j]) for j, sig in enumerate(MPPT) for t, *vals in frames]


def test_mppt_power_matches_the_notebook():
    from zelos_extension_photon.arrays import MpptPowerDeriver

    out = MpptPowerDeriver("B").split(7, 0, {"MPPT_Input_Voltage": 120.0, "MPPT_Input_Current": 2.0,
                                             "MPPT_Output_Voltage": 115.9, "MPPT_Output_Current": 0.5})
    assert out == [("MPPTPower_LilShah", (7, "Input", {"MPPT_B": 240.0})),
                   ("MPPTPower_LilShah", (7, "Output", {"MPPT_B": 57.95}))]
    events = MpptPowerDeriver("A").sources()["MPPTPower_LilShah"]
    assert {e: [(f.name, f.unit) for f in fs] for e, fs in events.items()} == {
        "Input": [("MPPT_A", "W"), ("MPPT_B", "W"), ("MPPT_C", "W")],
        "Output": [("MPPT_A", "W"), ("MPPT_B", "W"), ("MPPT_C", "W")]}


def test_mppt_power_uses_raw_frames_and_leaves_can_rows_alone():
    # 1 s level: averaged MPPT rows stay in the CAN source; power comes from raw frames (V and I from one frame)
    averaged = mppt_rows("A", [(0, 100.0, 1.0, 110.0, 0.5), (1000, 100.0, 1.0, 110.0, 0.5)])
    raw = mppt_rows("A", [(200, 100.0, 1.0, 110.0, 0.2), (800, 100.0, 3.0, 110.0, 0.8),
                          (1500, 90.0, 2.0, 112.0, 0.4)])
    client = FakeClient({2: averaged, 0: raw})
    schema = {"MPPT_A_PowerMeasurements": dict.fromkeys(MPPT, "")}
    main, power = FakeSource(), FakeSource()
    declare(main, schema, naming=can_naming())
    totals = pump(client, S, Window(0, 2000), 2, None, main, allowed_fields(schema, can_naming()), Progress(),
                  can_naming(), router=make_router(schema, 2, True), derived_sources={"MPPTPower_LilShah": power})
    assert [r[0] for r in main.records] == [0, 1_000_000_000]  # the CAN rows, not duplicated by raw frames
    assert totals["array_frames"] == 0
    # last raw frame per second: t=800 (100 V x 3 A, 110 V x 0.8 A) and t=1500 (90 x 2, 112 x 0.4)
    assert [(r[0] // 1_000_000, r[1], round(r[2]["MPPT_A"], 6)) for r in power.records] == [
        (800, "Input", 300.0), (800, "Output", 88.0), (1500, "Input", 180.0), (1500, "Output", 44.8)]


def test_mppt_power_at_raw_comes_from_the_main_stream():
    raw = mppt_rows("C", [(200, 100.0, 1.0, 110.0, 0.2), (200, 101.0, 1.0, 111.0, 0.3)])
    client = FakeClient({0: raw})
    schema = {"MPPT_C_PowerMeasurements": dict.fromkeys(MPPT, "")}
    main, power = FakeSource(), FakeSource()
    declare(main, schema, naming=can_naming())
    totals = pump(client, S, Window(0, 2000), 0, None, main, allowed_fields(schema, can_naming()), Progress(),
                  can_naming(), router=make_router(schema, 0, True), derived_sources={"MPPTPower_LilShah": power})
    assert len(client.calls) == 1 and len(main.records) == 2 and totals["rows"] == 8
    assert [round(r[2]["MPPT_C"], 6) for r in power.records if r[1] == "Output"] == [22.0, 33.3]


def test_write_trace_includes_mppt_power(tmp_path):
    rows = mppt_rows("A", [(500, 100.0, 1.0, 110.0, 0.5)])
    client = FakeClient({5: rows, 2: rows, 0: rows})
    out = write_trace(client, S, Window(0, 4000), 2, None, tmp_path / "t.trz", Progress())
    assert out["split_sources"] == ["MPPTPower_LilShah"] and out["split_records"] == 2


def test_live_loads_do_not_compute_mppt_power():
    rows = mppt_rows("A", [(500, 100.0, 1.0, 110.0, 0.5)])
    client = FakeClient({5: rows, 2: rows, 0: rows})
    made: dict[str, FakeSource] = {}
    res = LiveLoader(lambda: client, lambda name: made.setdefault(name, FakeSource())).load(
        S, Window(0, 4000), 2, None, Progress())
    assert res["split_records"] == 0 and list(made) == ["photon-20260927-211337-00"]
    assert len(client.calls) == 2  # discovery + the 1 s load; no extra raw fetch


def test_status_fields_get_dbc_labels():
    from zelos_extension_photon.loader import add_value_tables

    class Src:
        def __init__(self):
            self.tables = {}

        def add_value_table(self, event, field, table):
            self.tables[(event, field)] = table

    src = Src()
    schema = {"BPS_Status": {"BPS_Fault": "", "HV_Plus_Contactor_State": "", "Main_Battery_Voltage": "V"},
              "Pedal_Status": {"AccelPedal_Main_Pos": "%"}}
    assert add_value_tables(src, schema, can_naming()) == 2
    assert src.tables[("CarCAN/BPS_Status", "BPS_Fault")][4] == "Overtemperature"
    assert src.tables[("CarCAN/BPS_Status", "HV_Plus_Contactor_State")] == {0: "Open", 1: "Closed"}


def test_speed_in_mph_matches_photon():
    from zelos_extension_photon.arrays import SpeedDeriver

    # 500 rpm on a 21.56 in wheel = 32.07 mph; 10 m/s = 22.37 mph
    assert [(s, (t, e, {k: round(v, 2) for k, v in f.items()}))
            for s, (t, e, f) in SpeedDeriver("WheelRPM_FL").split(7, 0, {"RPM": 500.0, "Timestamp": 3.0})] == [
        ("Speed_LilShah", (7, "Speed", {"wheel_FL": 32.07}))]
    assert round(SpeedDeriver("MC_VelocityMeasurement").split(7, 0, {"MC_VehicleVelocity": 10.0})[0][1][2]["vehicle"],
                 2) == 22.37
    assert SpeedDeriver("WheelRPM_FR").split(7, 0, {"Timestamp": 1.0}) == []
    events = SpeedDeriver("WheelRPM_FR").sources()["Speed_LilShah"]
    assert [(f.name, f.unit) for f in events["Speed"]] == [("wheel_FL", "mph"), ("wheel_FR", "mph"), ("vehicle", "mph")]


def test_write_trace_includes_speed(tmp_path):
    rows = [Row(500, "WheelRPM_FL", "RPM", "rpm", 500.0), Row(500, "WheelRPM_FR", "RPM", "rpm", 510.0),
            Row(600, "MC_VelocityMeasurement", "MC_VehicleVelocity", "m/s", 14.0),
            Row(600, "MC_VelocityMeasurement", "MC_MotorVelocity", "rpm", 700.0)]
    client = FakeClient({5: rows, 2: rows, 0: rows})
    out = write_trace(client, S, Window(0, 4000), 2, None, tmp_path / "t.trz", Progress())
    assert out["split_sources"] == ["Speed_LilShah"] and out["split_records"] == 3
