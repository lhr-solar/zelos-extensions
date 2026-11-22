import threading
import tomllib
from pathlib import Path

import pytest

from zelos_extension_photon import ACTION_PREFIX, actions
from zelos_extension_photon.actions import STATE
from zelos_extension_photon.uploads import UploadRecord

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def stub_catalog(catalog, monkeypatch, tmp_path):
    STATE.configure({"timezone": "America/Chicago", "default_resolution": "1 s"})
    monkeypatch.setattr(STATE, "catalog", lambda refresh=False: catalog)
    # never touch the real ~/.photon-zelos record from tests
    monkeypatch.setattr(STATE, "uploads", UploadRecord(tmp_path / "uploads.json"))
    yield


def test_manifest_matches_runtime():
    m = tomllib.loads((ROOT / "extension.toml").read_text())
    assert m["name"] == ACTION_PREFIX
    assert (ROOT / m["agent"]["entry"]).exists()
    for p in m["package"]["paths"]:
        assert (ROOT / p).exists(), p


def test_registered_actions():
    names = []

    class Reg:
        def register(self, fn, name):
            names.append(name)

    actions.register_actions(Reg())
    assert names == ["cancel_job", "job_status", "list_sessions", "load_session", "load_session_advanced"]
    assert actions.list_sessions._action.read_only and actions.job_status._action.read_only
    assert not actions.load_session._action.read_only and not actions.load_session_advanced._action.read_only


def test_list_sessions_groups_days():
    out = actions.list_sessions(limit_days=2)
    assert out["total_sessions"] == 320 and len(out["days"]) == 2
    assert out["days"][0]["day"] == "2026-10-16"
    assert out["days"][0]["sessions"][0]["id"] == "20261017-012401-00"
    one = actions.list_sessions(day="2026-07-23")
    assert [d["day"] for d in one["days"]] == ["2026-07-23"]


def test_session_dropdown_depends_on_day():
    days = actions._day_choices()
    assert days[0].startswith("2026-10-16 (")
    july23 = next(d for d in days if d.startswith("2026-07-23"))
    sessions = actions._session_choices(july23)
    assert all(c[:8] == "20260723" or c[:8] == "20260724" for c in sessions)
    assert any(c.startswith("20260723-164704-00 |") for c in sessions)


def test_large_raw_load_is_refused_without_opt_in():
    with pytest.raises(ValueError, match="Allow large transfers"):
        actions._prepare("20260723-164704-00 | x", "raw", 0, None, None, False)
    s, level, window, sig = actions._prepare("20260723-164704-00", "raw", 30, 2, "BPS_Status.X, A.B", False)
    assert level == 0 and window.duration_s == 120 and sig == ["BPS_Status.X", "A.B"]


def test_unknown_session_is_a_clear_error():
    with pytest.raises(ValueError, match="no session"):
        actions._prepare("nope | label", "1 s", 0, None, None, False)


def test_only_one_job_at_a_time():
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return {"ok": True}

    t = threading.Thread(target=lambda: STATE.run_job("load", "A", slow))
    t.start()
    started.wait(5)
    with pytest.raises(RuntimeError, match="Busy with load of A"):
        STATE.run_job("upload", "B", lambda: {})
    release.set()
    t.join(5)
    assert STATE.progress.snapshot()["phase"] == "done"


def test_long_job_returns_running_then_status_has_result():
    release = threading.Event()

    def slow():
        release.wait(5)
        return {"url": "u"}

    out = STATE.run_job("upload", "A", slow, wait_s=0.05)
    assert out["status"] == "running" and out["job"] == "upload"
    release.set()
    for _ in range(100):
        if actions.job_status()["phase"] == "done":
            break
        threading.Event().wait(0.05)
    assert actions.job_status()["result"] == {"url": "u"}


def test_failed_job_raises_and_reports():
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        STATE.run_job("load", "A", boom)
    snap = actions.job_status()
    assert snap["phase"] == "failed" and "nope" in snap["detail"]


def test_trace_paths_are_readable_and_distinct(catalog, tmp_path):
    STATE.configure({"timezone": "America/Chicago", "traces_dir": str(tmp_path)})
    s = next(c for c in catalog if c.id == "20260723-164704-00")
    from zelos_extension_photon.sessions import plan_window

    whole = STATE.trace_path(s, "1 s", True, plan_window(s, None, None))
    part = STATE.trace_path(s, "100 ms", False, plan_window(s, 60, 10))
    assert whole == tmp_path / "2026-07-23 1147 20260723-164704-00 1s.trz"
    assert part.name == "2026-07-23 1147 20260723-164704-00 100ms from 124704 10min.trz"


def test_live_destination_refuses_old_data(monkeypatch):
    monkeypatch.setattr(STATE, "run_job", lambda *a, **k: pytest.fail("should not start a job"))
    with pytest.raises(ValueError, match="Zelos Cloud or a Trace file instead"):
        actions.load_session_advanced("20260723-164704-00", destination=actions.DEST_LIVE)


def test_load_to_a_trace_file(monkeypatch, tmp_path):
    STATE.configure({"timezone": "America/Chicago", "traces_dir": str(tmp_path)})
    written = {}

    def fake_write(client, s, window, level, sig, path, progress, naming=None):
        path.write_bytes(b"trz")
        written.update(level=level, path=path)
        return {"rows": 1, "events": 1, "dropped": 0}

    monkeypatch.setattr(actions, "write_trace", fake_write)
    monkeypatch.setattr(STATE, "client", lambda: None)
    out = actions.load_session_advanced("20261017-012401-00 | label", resolution="100 ms", destination="Trace file")
    assert out["destination"] == "trace file" and written["level"] == 1
    assert out["path"] == str(written["path"]) and written["path"].parent == tmp_path


@pytest.fixture
def fake_cloud(monkeypatch):
    STATE.configure({"timezone": "America/Chicago", "organization": "lhrs"})
    calls = {"uploads": [], "opened": []}
    monkeypatch.setattr(STATE, "opener", lambda org, tid: calls["opened"].append((org, tid)) or True)
    monkeypatch.setattr(STATE, "client", lambda: None)
    monkeypatch.setattr(STATE, "agent_factory", lambda: None)

    def fake_upload(client, agent, s, window, level, sig, name, extra_tags, org, progress, workdir, naming=None):
        calls.setdefault("naming", []).append(naming)
        n = len(calls["uploads"]) + 1
        calls["uploads"].append({"name": name, "tags": ["Photon", *extra_tags], "org": org, "level": level})
        return {"url": f"https://console2.zeloscloud.io/lhrs/traces/t{n}", "trace_id": f"t{n}", "name": name,
                "tags": ["Photon", *extra_tags]}

    monkeypatch.setattr(actions, "build_and_upload", fake_upload)
    return calls


def test_load_defaults_to_zelos_cloud_and_opens_it(fake_cloud):
    out = actions.load_session("20261017-012401-00 | label")
    assert out["destination"] == "zelos cloud" and not out["reused"]
    assert out["tags"] == ["Photon"] and out["opened_in_app"]
    assert fake_cloud["opened"] == [("lhrs", "t1")]
    assert fake_cloud["uploads"][0]["name"] == "2026-10-16 20:24 CDT 20261017-012401-00 (1 s)"


def test_loading_the_same_thing_again_reuses_the_trace(fake_cloud):
    first = actions.load_session("20261017-012401-00 | label")
    again = actions.load_session("20261017-012401-00 | other label text")
    assert again["reused"] and again["trace_id"] == first["trace_id"] == "t1"
    assert len(fake_cloud["uploads"]) == 1 and fake_cloud["opened"] == [("lhrs", "t1"), ("lhrs", "t1")]
    # a different resolution or window is a different trace
    actions.load_session("20261017-012401-00 | label", resolution="100 ms")
    actions.load_session("20261017-012401-00 | label", start_offset_min=1, duration_min=2)
    assert len(fake_cloud["uploads"]) == 3
    assert fake_cloud["uploads"][2]["name"].endswith("(1 s) from 20:25 2 min")
    # Upload again forces a fresh trace
    fresh = actions.load_session_advanced("20261017-012401-00 | label", new_upload=True)
    assert not fresh["reused"] and fresh["trace_id"] == "t4"


def test_cloud_load_without_opening_and_with_extra_tags(fake_cloud):
    out = actions.load_session_advanced("20261017-012401-00 | label", open_in_app=False, extra_tags="Brainerd, dyno")
    assert not out["opened_in_app"] and fake_cloud["opened"] == []
    assert out["tags"] == ["Photon", "Brainerd", "dyno"]


def test_unknown_destination_is_refused(fake_cloud):
    with pytest.raises(ValueError, match="Unknown destination"):
        actions.load_session_advanced("20261017-012401-00 | label", destination="Somewhere")


def test_loads_use_the_can_extensions_names(fake_cloud):
    actions.load_session("20261017-012401-00 | label")
    n = fake_cloud["naming"][0]
    assert (n.source, n.bus, n.event_type) == ("CAN", "CarCAN", "zelos.can.message.v1")
    STATE.configure({"timezone": "America/Chicago", "organization": "lhrs", "can_bus": "Car CAN 2"})
    actions.load_session("20261017-012401-00 | label")
    assert fake_cloud["naming"][1].bus == "Car CAN 2"
    assert len(fake_cloud["uploads"]) == 2  # a different naming is a different trace


def _fields(fn):
    import json

    schema = fn._action.to_schema_json({}) if hasattr(fn._action, "to_schema_json") else None
    props = json.loads(schema)["properties"] if schema else fn._action.to_schema({})["properties"]
    return list(props)


def test_short_form_has_only_the_everyday_fields_in_order():
    assert _fields(actions.load_session) == ["day", "session", "resolution", "start_offset_min", "duration_min"]
    adv = _fields(actions.load_session_advanced)
    assert adv[:5] == ["day", "session", "resolution", "start_offset_min", "duration_min"]
    assert adv[5] == "destination" and set(adv[5:]) == {"destination", "signals", "allow_large", "name",
                                                        "extra_tags", "open_in_app", "new_upload"}


def test_short_load_goes_to_cloud_and_opens(fake_cloud):
    out = actions.load_session("20261017-012401-00 | label", resolution="100 ms", start_offset_min=1, duration_min=2)
    assert out["destination"] == "zelos cloud" and out["opened_in_app"] and fake_cloud["uploads"][0]["level"] == 1


def test_older_trace_formats_are_not_reused(fake_cloud, monkeypatch):
    first = actions.load_session("20261017-012401-00 | label")
    monkeypatch.setattr(actions, "TRACE_FORMAT", actions.TRACE_FORMAT + 1)
    second = actions.load_session("20261017-012401-00 | label")
    assert not second["reused"] and second["trace_id"] != first["trace_id"]
