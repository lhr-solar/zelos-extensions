import threading
import tomllib
from pathlib import Path

import pytest

from zelos_extension_photon import ACTION_PREFIX, actions
from zelos_extension_photon.actions import STATE

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def stub_catalog(catalog, monkeypatch):
    STATE.configure({"timezone": "America/Chicago", "default_resolution": "1 s"})
    monkeypatch.setattr(STATE, "catalog", lambda refresh=False: catalog)
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
    assert names == ["cancel_job", "job_status", "list_sessions", "load_session", "upload_session"]
    assert actions.list_sessions._action.read_only and actions.job_status._action.read_only
    assert not actions.load_session._action.read_only and not actions.upload_session._action.read_only


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
    with pytest.raises(ValueError, match="Trace file instead"):
        actions.load_session("20260723-164704-00", destination=actions.DESTINATIONS[1])


def test_load_defaults_to_a_trace_file(monkeypatch, tmp_path):
    STATE.configure({"timezone": "America/Chicago", "traces_dir": str(tmp_path)})
    written = {}

    def fake_write(client, s, window, level, sig, path, progress):
        path.write_bytes(b"trz")
        written.update(level=level, path=path)
        return {"rows": 1, "events": 1, "dropped": 0}

    monkeypatch.setattr(actions, "write_trace", fake_write)
    monkeypatch.setattr(STATE, "client", lambda: None)
    out = actions.load_session("20261017-012401-00 | label", resolution="100 ms")
    assert out["destination"] == "trace file" and written["level"] == 1
    assert out["path"] == str(written["path"]) and written["path"].parent == tmp_path


def test_upload_opens_the_new_trace_in_the_app(monkeypatch):
    STATE.configure({"timezone": "America/Chicago", "organization": "lhrs"})
    opened = []
    monkeypatch.setattr(STATE, "opener", lambda org, tid: opened.append((org, tid)) or True)
    monkeypatch.setattr(STATE, "client", lambda: None)
    monkeypatch.setattr(STATE, "agent_factory", lambda: None)
    monkeypatch.setattr(actions, "build_and_upload", lambda *a, **k: {
        "url": "https://console2.zeloscloud.io/lhrs/traces/abc", "trace_id": "abc", "tags": ["Photon"]})
    out = actions.upload_session("20261017-012401-00 | label")
    assert out["opened_in_app"] and opened == [("lhrs", "abc")] and out["tags"] == ["Photon"]
    out = actions.upload_session("20261017-012401-00 | label", open_in_app=False)
    assert not out["opened_in_app"] and len(opened) == 1
