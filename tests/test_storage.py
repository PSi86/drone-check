import json

from drone_check.model import DroneSnapshot, Evaluation
from drone_check.storage import render_report, save_capture


def _snapshot(pilot_name="", craft_name=""):
    snap = DroneSnapshot(captured_at="2026-06-17T10-00-00Z", uid="uid123")
    snap.pilot_name = pilot_name
    snap.craft_name = craft_name
    snap.firmware.variant = "BTFL"
    snap.firmware.version = "4.5.1"
    return snap


def test_folder_name_uses_fc_names(tmp_path):
    out = save_capture(
        tmp_path, _snapshot("Max Power", "TestQuad"), Evaluation(passed=True),
        {"dump all": "set craft_name = TestQuad\n"}, "2026-06-17T10-00-00Z",
    )
    assert out.parent == tmp_path
    assert out.name == "2026-06-17T10-00-00Z_Max_Power_TestQuad"
    assert (out / "snapshot.json").exists()
    assert (out / "evaluation.json").exists()
    assert (out / "report.txt").exists()
    assert (out / "raw" / "dump_all.txt").exists()


def test_report_and_snapshot_carry_names_from_fc(tmp_path):
    out = save_capture(
        tmp_path, _snapshot("Anna", "WingOne"), Evaluation(passed=True), {}, "ts",
    )
    snap = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["pilot_name"] == "Anna" and snap["craft_name"] == "WingOne"
    assert "pilot" not in snap  # the old operator field is gone
    report = (out / "report.txt").read_text(encoding="utf-8")
    assert "Pilot_Name : Anna" in report
    assert "Craft_Name : WingOne" in report


def test_missing_names_become_unknown(tmp_path):
    out = save_capture(tmp_path, _snapshot("", ""), Evaluation(passed=False), {}, "ts1")
    assert out.name == "ts1_unknown_unknown"


def test_fallback_used_for_folder_only_not_data(tmp_path):
    out = save_capture(
        tmp_path, _snapshot("", "Quad"), Evaluation(passed=True), {}, "ts2",
        pilot_fallback="OperatorBob",
    )
    # folder label uses the fallback ...
    assert out.name == "ts2_OperatorBob_Quad"
    # ... but the captured data keeps the FC truth (empty pilot_name)
    snap = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["pilot_name"] == ""


def test_custom_template(tmp_path):
    out = save_capture(
        tmp_path, _snapshot("Max", "Quad"), Evaluation(passed=True), {}, "ts",
        folder_template="{uid}_{craft_name}",
    )
    assert out.name == "uid123_Quad"


def test_existing_folder_is_not_overwritten(tmp_path):
    snap = _snapshot("Max", "Quad")
    a = save_capture(tmp_path, snap, Evaluation(passed=True), {}, "ts")
    b = save_capture(tmp_path, snap, Evaluation(passed=True), {}, "ts")
    assert a != b
    assert b.name.endswith("-2")
