from pathlib import Path

import pytest
from conftest import FakeClient, FakeSource

from zelos_extension_photon import loader
from zelos_extension_photon.loader import (
    PHOTON_TAG,
    Cancelled,
    LiveLoader,
    Progress,
    allowed_fields,
    build_and_upload,
    declare,
    group_rows,
    pump,
)
from zelos_extension_photon.photon import CatalogSession, Row
from zelos_extension_photon.sessions import Window

S = CatalogSession("20260723-164704-00", 1000, 1000 + 120_000, 0, False, "")


def rows_for(t0: int, n: int, step: int) -> list[Row]:
    """Two messages; every signal of a message shares the frame timestamp (as Photon stores them)."""
    out = []
    for sig, unit in (("Main_Battery_Voltage", "V"), ("Main_Battery_Current", "A")):
        out += [Row(t0 + i * step, "BPS_Status", sig, unit, float(i)) for i in range(n)]
    out += [Row(t0 + i * step + 3, "Temp 2.Center", "Temperature", "c", 20.0 + i) for i in range(n)]
    return out


def test_group_rows_merges_signals_per_frame():
    recs, seen, dropped = group_rows(rows_for(1000, 5, 100))
    assert seen == 15 and dropped == 0
    bps = [r for r in recs if r[1] == "BPS_Status"]
    assert len(bps) == 5
    assert bps[0] == (1000 * 1_000_000, "BPS_Status", {"Main_Battery_Voltage": 0.0, "Main_Battery_Current": 0.0})
    assert [r[0] for r in recs] == sorted(r[0] for r in recs)
    # names that break Zelos' allow-list are sanitized
    assert {r[1] for r in recs} == {"BPS_Status", "Temp 2_Center"}


def test_group_rows_drops_fields_the_schema_cannot_hold():
    recs, _, dropped = group_rows(rows_for(1000, 2, 100), {"BPS_Status": {"Main_Battery_Voltage"}})
    assert dropped == 4  # 2 current rows + 2 temperature rows
    assert all(set(r[2]) == {"Main_Battery_Voltage"} for r in recs)


def test_declare_one_event_per_message_with_units():
    src = FakeSource()
    new = declare(src, {"BPS_Status": {"Main_Battery_Voltage": "V", "Main_Battery_Current": "A"},
                        "Odd.Name": {"x": ""}})
    assert new == {"BPS_Status", "Odd_Name"}
    assert src.events["BPS_Status"] == [("Main_Battery_Current", "A"), ("Main_Battery_Voltage", "V")]
    assert src.events["Odd_Name"] == [("x", None)]
    assert declare(src, {"BPS_Status": {"y": ""}}, set(new)) == set()  # never re-declares


def test_pump_chunks_and_logs_everything():
    client = FakeClient({1: rows_for(1000, 1200, 100)})  # 120 s of 10 Hz
    src = FakeSource()
    schema = {"BPS_Status": {"Main_Battery_Voltage": "V", "Main_Battery_Current": "A"},
              "Temp 2.Center": {"Temperature": "c"}}
    declare(src, schema)
    p = Progress()
    totals = pump(client, S, Window(1000, 1000 + 120_001), 1, None, src, allowed_fields(schema), p)
    assert totals == {"rows": 3600, "events": 2400, "dropped": 0}
    assert len(src.records) == 2400 and src.flushes == 1
    assert p.snapshot()["chunks"] == "1/1"


def test_pump_honours_cancel():
    client = FakeClient({0: rows_for(1000, 10, 100)})
    p = Progress()
    p.cancel.set()
    with pytest.raises(Cancelled):
        pump(client, S, Window(1000, 200_000), 0, None, FakeSource(), {}, p)


def test_live_loader_discovers_once_per_session():
    client = FakeClient({5: rows_for(1000, 3, 30_000), 2: rows_for(1000, 100, 1000)})
    made: dict[str, FakeSource] = {}
    live = LiveLoader(lambda: client, lambda name: made.setdefault(name, FakeSource()))
    res = live.load(S, Window(1000, 121_001), 2, None, Progress())
    assert res["source"] == "photon-20260723-164704-00"
    assert res["events"] == 200
    live.load(S, Window(1000, 50_000), 2, None, Progress())
    assert sum(1 for c in client.calls if c[3] == 5) == 1  # discovery ran once
    assert len(made) == 1


class FakeAgent:
    def __init__(self, fail_tags: bool = False):
        self.fail_tags = fail_tags
        self.uploads: list[dict] = []

    def trace(self, path):
        agent = self

        class T:
            def export(self, out, format, overwrite):
                assert format == "trz2"
                Path(out).write_bytes(Path(path).read_bytes()[:10] or b"x")
                agent.sealed = out

        return T()

    def close_traces(self, paths):
        pass

    def upload(self, path, name, tags, organization, timeout):
        if self.fail_tags:
            raise ValueError(f"unknown tag(s): {', '.join(tags)}\navailable tags: (none)")
        self.uploads.append({"path": path, "name": name, "tags": tags, "organization": organization})

        class U:
            url = "https://console2.zeloscloud.io/lhrs/traces/abc"
            trace_id = "abc"

        return U()


def _upload(agent, extra_tags, tmp_path):
    client = FakeClient({5: rows_for(1000, 3, 30_000), 1: rows_for(1000, 50, 100)})
    return build_and_upload(client, agent, S, Window(1000, 121_001), 1, None, "my trace", extra_tags, "lhrs",
                            Progress(), tmp_path)


def test_every_upload_is_tagged_photon(tmp_path):
    agent = FakeAgent()
    res = _upload(agent, [], tmp_path)
    assert agent.uploads[0]["tags"] == [PHOTON_TAG]
    res = _upload(agent, ["Brainerd", "photon", ""], tmp_path)
    assert agent.uploads[1]["tags"] == [PHOTON_TAG, "Brainerd"]  # no duplicate, Photon always first
    assert res["url"].endswith("/abc") and res["tags"][0] == "Photon"
    assert list(tmp_path.iterdir()) == []  # temp trace files are cleaned up


def test_missing_photon_tag_explains_how_to_fix(tmp_path):
    with pytest.raises(ValueError, match="no 'Photon' tag"):
        _upload(FakeAgent(fail_tags=True), [], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_upload_trace_is_written_in_its_own_namespace(monkeypatch, tmp_path):
    seen = {}
    real = loader.zelos_sdk.TraceSource

    def spy(name, namespace=None, **kw):
        seen["name"], seen["ns"] = name, namespace
        return real(name, namespace=namespace, **kw)

    monkeypatch.setattr(loader.zelos_sdk, "TraceSource", spy)
    _upload(FakeAgent(), [], tmp_path)
    assert seen["name"] == "CAN" and seen["ns"] is not None


def test_write_trace_produces_a_trace_file(tmp_path):
    from zelos_extension_photon.loader import write_trace

    client = FakeClient({5: rows_for(1000, 3, 30_000), 1: rows_for(1000, 50, 100)})
    path = tmp_path / "sub" / "t.trz"
    path.parent.mkdir()
    path.write_bytes(b"old")  # replaced, not appended
    totals = write_trace(client, S, Window(1000, 121_001), 1, None, path, Progress())
    assert totals["events"] == 100 and path.stat().st_size > 1000


def test_write_trace_removes_partial_file_on_cancel(tmp_path):
    from zelos_extension_photon.loader import write_trace

    client = FakeClient({5: rows_for(1000, 3, 30_000), 1: rows_for(1000, 50, 100)})
    p = Progress()
    p.cancel.set()
    path = tmp_path / "t.trz"
    with pytest.raises(Cancelled):
        write_trace(client, S, Window(1000, 121_001), 1, None, path, p)
    assert not path.exists()


def test_can_naming_matches_the_can_extension():
    from zelos_extension_photon.loader import CAN_MESSAGE_EVENT_TYPE, can_naming

    src = FakeSource()
    naming = can_naming()
    declare(src, {"Pedal_Status": {"AccelPedal_Main_Pos": "%"}}, naming=naming)
    # High Noon binds {source: CAN, message: CarCAN/Pedal_Status, signal: AccelPedal_Main_Pos,
    #                  eventType: zelos.can.message.v1}
    assert src.events == {"CarCAN/Pedal_Status": [("AccelPedal_Main_Pos", "%")]}
    assert src.event_types == {"CarCAN/Pedal_Status": CAN_MESSAGE_EVENT_TYPE}
    recs, _, _ = group_rows([Row(5, "Pedal_Status", "AccelPedal_Main_Pos", "%", 12.5)], naming=naming)
    assert recs == [(5_000_000, "CarCAN/Pedal_Status", {"AccelPedal_Main_Pos": 12.5})]
    assert naming.label == "CAN/CarCAN"


def test_live_loads_keep_their_own_source():
    client = FakeClient({5: rows_for(1000, 3, 30_000), 2: rows_for(1000, 10, 1000)})
    made: dict[str, FakeSource] = {}
    LiveLoader(lambda: client, lambda name: made.setdefault(name, FakeSource())).load(
        S, Window(1000, 121_001), 2, None, Progress())
    (name, src), = made.items()
    assert name == "photon-20260723-164704-00"
    assert "BPS_Status" in src.events and all(t is None for t in src.event_types.values())
