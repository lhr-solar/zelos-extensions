import pytest

from zelos_extension_photon import photon
from zelos_extension_photon.photon import PhotonClient, PhotonError, parse_catalog, parse_row


def test_parse_row():
    r = parse_row(b"1792200241000,Suspension_Back,SUS_Data4,mm,34.7140958")
    assert (r.t_ms, r.message, r.signal, r.unit, r.value) == (1792200241000, "Suspension_Back", "SUS_Data4",
                                                             "mm", 34.7140958)
    assert parse_row(b"") is None
    assert parse_row(b"1,a,b,,nan-ish") is None
    assert parse_row(b"1,a,b") is None


def test_parse_catalog_skips_bad_rows():
    out = parse_catalog({"sessions": [{"id": "b", "min_ms": 5, "max_ms": 9}, {"id": "a"},
                                      {"id": "c", "min_ms": 1, "max_ms": 2, "has_video": True}]})
    assert [s.id for s in out] == ["c", "b"]
    assert out[0].has_video and out[1].dbc_hash == ""


def test_password_required():
    with pytest.raises(PhotonError, match="password"):
        PhotonClient("https://example.invalid", "")


def test_ws_url_from_base():
    assert PhotonClient("https://photon-dev.lhrsolar.org/", "pw").ws_url == "wss://photon-dev.lhrsolar.org/ws"
    assert PhotonClient("http://127.0.0.1:8080", "pw").ws_url == "ws://127.0.0.1:8080/ws"


class _Resp:
    def __init__(self, status, lines):
        self.status_code, self._lines, self.text = status, lines, "nope"

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_stream_rows_sends_query_and_parses(monkeypatch):
    c = PhotonClient("https://h", "pw")
    seen = {}

    def get(url, params, stream, timeout):
        seen.update(url=url, params=params)
        return _Resp(200, [b"t_ms,message,signal,unit,value", b"10,M,S,V,1.5", b"", b"11,M,S,V,2"])

    monkeypatch.setattr(c._http, "get", get)
    rows = list(c.stream_rows("sess", 1, 2, 1, ["M.S"]))
    assert [r.value for r in rows] == [1.5, 2.0]
    assert seen["url"] == "https://h/export.csv"
    assert seen["params"] == {"session": "sess", "from": 1, "to": 2, "level": 1, "signals": "M.S"}
    assert c._http.headers["Authorization"].startswith("Basic ")


def test_stream_rows_401_is_a_clear_error(monkeypatch):
    c = PhotonClient("https://h", "pw")
    monkeypatch.setattr(c._http, "get", lambda *a, **k: _Resp(401, []))
    with pytest.raises(PhotonError, match="refused the password"):
        list(c.stream_rows("s", 1, 2, 0))


def test_stream_rows_retries_connection_errors(monkeypatch):
    c = PhotonClient("https://h", "pw", retries=3)
    attempts = []

    def get(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise photon.requests.ConnectionError("reset")
        return _Resp(200, [b"t_ms,message,signal,unit,value", b"1,M,S,,3"])

    monkeypatch.setattr(c._http, "get", get)
    monkeypatch.setattr(photon.time, "sleep", lambda s: None)
    assert [r.value for r in c.stream_rows("s", 0, 9, 2)] == [3.0]
    assert len(attempts) == 3
