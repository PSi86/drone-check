"""Read back captures stored under the log directory.

Captures are immutable folders written by :func:`storage.save_capture`. This
module only *reads* them — it never modifies or moves anything — to power the
web UI's log-overview page.

A folder is treated as a capture when it contains a ``snapshot.json``. Folders
whose ``snapshot.json`` is malformed, or that are missing ``evaluation.json``,
are still listed (with the affected fields left ``None``) rather than hidden:
the overview must reflect what is actually on disk, not silently drop real
captures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# storage.save_capture() sanitises the command "dump all" to this filename.
DUMP_FILENAME = "dump_all.txt"


@dataclass
class CaptureSummary:
    """One row of the log overview, derived from a stored capture folder."""

    id: str  # folder name; stable identifier within the log directory
    folder: str  # absolute path on disk
    captured_at: Optional[str] = None
    pilot_name: Optional[str] = None
    craft_name: Optional[str] = None
    uid: Optional[str] = None
    # {"variant", "version", "git_hash"} — empty strings when unknown.
    firmware: dict[str, str] = field(default_factory=dict)
    # Overall verdict from evaluation.json; None when it is missing/unreadable.
    verdict: Optional[bool] = None
    firmware_hash_approved: Optional[bool] = None
    firmware_hash_source: Optional[str] = None
    # True when raw/dump_all.txt exists (required to load into SITL later).
    has_dump: bool = False
    # False when snapshot.json is missing or could not be parsed.
    readable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> Optional[dict]:
    """Parse a JSON file, returning None on any read/parse error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _summarise(folder: Path) -> CaptureSummary:
    snapshot = _read_json(folder / "snapshot.json")
    evaluation = _read_json(folder / "evaluation.json")

    summary = CaptureSummary(id=folder.name, folder=str(folder))
    summary.has_dump = (folder / "raw" / DUMP_FILENAME).is_file()

    if snapshot is None:
        summary.readable = False
        return summary

    fw = snapshot.get("firmware") or {}
    summary.captured_at = snapshot.get("captured_at")
    summary.pilot_name = snapshot.get("pilot_name")
    summary.craft_name = snapshot.get("craft_name")
    summary.uid = snapshot.get("uid")
    summary.firmware = {
        "variant": fw.get("variant", ""),
        "version": fw.get("version", ""),
        "git_hash": fw.get("git_hash", ""),
    }
    summary.firmware_hash_approved = snapshot.get("firmware_hash_approved")
    summary.firmware_hash_source = snapshot.get("firmware_hash_source")

    if evaluation is not None:
        summary.verdict = evaluation.get("passed")

    return summary


def list_captures(log_dir: Path) -> list[CaptureSummary]:
    """List every capture folder under ``log_dir``, newest first.

    A folder qualifies when it contains a ``snapshot.json``; session ``*.log``
    files and other stray entries are ignored. Sorting is by capture timestamp
    (which the folder name also leads with) descending, so the most recent
    capture appears at the top.
    """
    base = Path(log_dir)
    if not base.is_dir():
        return []

    summaries: list[CaptureSummary] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if not (entry / "snapshot.json").is_file():
            continue
        summaries.append(_summarise(entry))

    summaries.sort(key=lambda s: (s.captured_at or "", s.id), reverse=True)
    return summaries


def resolve_capture_dir(log_dir: Path, capture_id: str) -> Path:
    """Resolve a capture id to its folder, guarding against path traversal.

    The id must name a *direct* child directory of ``log_dir``; anything that
    escapes (``..``, absolute paths, nested separators) raises ``ValueError``.
    """
    base = Path(log_dir).resolve()
    target = (base / capture_id).resolve()
    if target.parent != base or not target.is_dir():
        raise ValueError(f"invalid capture id: {capture_id!r}")
    return target


def open_in_file_manager(path: Path) -> None:
    """Open ``path`` in the OS file manager.

    Safe to call from the server because ``serve`` runs locally — the browser
    and the server share the operator's machine.
    """
    path = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606 (Windows only)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
