"""Read-back of stored capture folders for the log-overview page."""

import json
from pathlib import Path

import pytest

from drone_check import captures


def _make_capture(base: Path, name: str, *, snapshot=None, evaluation=None, dump=True) -> Path:
    folder = base / name
    (folder / "raw").mkdir(parents=True)
    if snapshot is not None:
        (folder / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    if evaluation is not None:
        (folder / "evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
    if dump:
        (folder / "raw" / captures.DUMP_FILENAME).write_text("batch start\nsave\n", encoding="utf-8")
    return folder


def _snapshot(captured_at, pilot="P", craft="C", variant="BTFL", version="4.4.0"):
    return {
        "captured_at": captured_at,
        "uid": "abc123",
        "pilot_name": pilot,
        "craft_name": craft,
        "firmware": {"variant": variant, "version": version, "git_hash": "deadbeef"},
        "firmware_hash_approved": True,
        "firmware_hash_source": "github",
    }


def test_lists_captures_newest_first(tmp_path):
    _make_capture(tmp_path, "2026-06-17T10-00-00Z_a", snapshot=_snapshot("2026-06-17T10-00-00Z"),
                  evaluation={"passed": True})
    _make_capture(tmp_path, "2026-06-17T12-00-00Z_b", snapshot=_snapshot("2026-06-17T12-00-00Z"),
                  evaluation={"passed": False})

    items = captures.list_captures(tmp_path)

    assert [c.captured_at for c in items] == ["2026-06-17T12-00-00Z", "2026-06-17T10-00-00Z"]
    assert items[0].verdict is False
    assert items[1].verdict is True
    assert items[0].firmware == {"variant": "BTFL", "version": "4.4.0", "git_hash": "deadbeef"}
    assert all(c.has_dump for c in items)
    assert all(c.readable for c in items)


def test_missing_evaluation_yields_unknown_verdict(tmp_path):
    _make_capture(tmp_path, "cap", snapshot=_snapshot("2026-06-17T10-00-00Z"), evaluation=None)
    (item,) = captures.list_captures(tmp_path)
    assert item.verdict is None
    assert item.readable is True


def test_corrupt_snapshot_is_listed_but_marked_unreadable(tmp_path):
    folder = tmp_path / "broken"
    (folder / "raw").mkdir(parents=True)
    (folder / "snapshot.json").write_text("{ not json", encoding="utf-8")

    (item,) = captures.list_captures(tmp_path)
    assert item.id == "broken"
    assert item.readable is False
    assert item.verdict is None
    assert item.has_dump is False


def test_non_capture_entries_ignored(tmp_path):
    # Session log files and folders without snapshot.json must not appear.
    (tmp_path / "session-20260617.log").write_text("x", encoding="utf-8")
    (tmp_path / "stray_folder").mkdir()
    _make_capture(tmp_path, "real", snapshot=_snapshot("2026-06-17T10-00-00Z"),
                  evaluation={"passed": True})

    items = captures.list_captures(tmp_path)
    assert [c.id for c in items] == ["real"]


def test_missing_log_dir_returns_empty(tmp_path):
    assert captures.list_captures(tmp_path / "does-not-exist") == []


def test_resolve_capture_dir_accepts_direct_child(tmp_path):
    folder = _make_capture(tmp_path, "cap", snapshot=_snapshot("t"), evaluation={"passed": True})
    assert captures.resolve_capture_dir(tmp_path, "cap") == folder.resolve()


@pytest.mark.parametrize("bad_id", ["..", "../secret", "cap/raw", "", "nope"])
def test_resolve_capture_dir_rejects_traversal_and_unknown(tmp_path, bad_id):
    _make_capture(tmp_path, "cap", snapshot=_snapshot("t"), evaluation={"passed": True})
    with pytest.raises(ValueError):
        captures.resolve_capture_dir(tmp_path, bad_id)
