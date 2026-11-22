from zelos_extension_photon.uploads import UploadRecord, upload_key


def test_key_ignores_signal_order_and_separates_orgs():
    a = upload_key("s", 2, 1, 9, ["B.x", "A.y"], "lhrs")
    assert a == upload_key("s", 2, 1, 9, ["A.y", "B.x"], "lhrs")
    assert a != upload_key("s", 2, 1, 9, ["A.y", "B.x"], "other")
    assert upload_key("s", 2, 1, 9, None, None) == "-|photon|s|L2|1-9|all"
    # a naming change means a different trace, never a reuse of one with old signal names
    assert upload_key("s", 2, 1, 9, None, "lhrs", "CAN/CarCAN") != upload_key("s", 2, 1, 9, None, "lhrs")


def test_record_round_trip_and_survives_garbage(tmp_path):
    rec = UploadRecord(tmp_path / "sub" / "uploads.json")
    assert rec.get("k") is None
    rec.put("k", {"trace_id": "t1", "url": "u"})
    assert UploadRecord(tmp_path / "sub" / "uploads.json").get("k")["trace_id"] == "t1"
    rec.forget("k")
    assert rec.get("k") is None
    (tmp_path / "sub" / "uploads.json").write_text("not json")
    assert rec.get("k") is None  # a corrupt record is ignored, not fatal
