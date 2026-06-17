"""Persist captured data to disk.

Each capture is written **once** into its own folder and is never modified or
moved afterwards — the files hold only the real data read from the flight
controller. The folder name is derived from a configurable template, by default::

    logs/<timestamp>_<pilot_name>_<craft_name>/
        snapshot.json     normalised DroneSnapshot
        evaluation.json   rule results + verdict
        report.txt        human-readable summary
        raw/<command>.txt  raw CLI output for every captured command

``pilot_name`` and ``craft_name`` come from the flight controller. A
``pilot_fallback`` may be supplied for the folder label only (e.g. operator
input when the FC reports no pilot name); it never enters the captured files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .model import DroneSnapshot, Evaluation

DEFAULT_FOLDER_TEMPLATE = "{timestamp}_{pilot_name}_{craft_name}"

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name: str, fallback: str) -> str:
    cleaned = _SAFE_RE.sub("_", (name or "").strip()).strip("_")
    return cleaned or fallback


def _folder_name(
    snapshot: DroneSnapshot,
    timestamp: str,
    template: str,
    pilot_fallback: str,
) -> str:
    """Render the capture folder name from the template, sanitising each field."""
    fields = {
        "timestamp": _safe(timestamp, "capture"),
        "pilot_name": _safe(snapshot.pilot_name or pilot_fallback, "unknown"),
        "craft_name": _safe(snapshot.craft_name, "unknown"),
        "uid": _safe(snapshot.uid, "unknown_fc"),
        "variant": _safe(snapshot.firmware.variant, "unknown"),
        "version": _safe(snapshot.firmware.version, "unknown"),
    }
    try:
        name = template.format(**fields)
    except (KeyError, IndexError):
        # Bad template -> fall back to the documented default rather than crash.
        name = DEFAULT_FOLDER_TEMPLATE.format(**fields)
    return _safe(name, fields["timestamp"])


def save_capture(
    base_dir: Path,
    snapshot: DroneSnapshot,
    evaluation: Evaluation,
    raw_cli: dict[str, str],
    timestamp: str,
    folder_template: str = DEFAULT_FOLDER_TEMPLATE,
    pilot_fallback: str = "",
) -> Path:
    """Write all artefacts for one capture and return the created directory.

    The directory is created fresh; if the chosen name already exists a numeric
    suffix is appended so an existing capture is never overwritten.
    """
    base_dir = Path(base_dir)
    name = _folder_name(snapshot, timestamp, folder_template, pilot_fallback)

    out = base_dir / name
    suffix = 2
    while out.exists():
        out = base_dir / f"{name}-{suffix}"
        suffix += 1
    (out / "raw").mkdir(parents=True, exist_ok=True)

    (out / "snapshot.json").write_text(
        json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8"
    )
    (out / "evaluation.json").write_text(
        json.dumps(evaluation.to_dict(), indent=2), encoding="utf-8"
    )
    (out / "report.txt").write_text(
        render_report(snapshot, evaluation), encoding="utf-8"
    )

    for command, text in raw_cli.items():
        fname = _safe(command, "command") + ".txt"
        (out / "raw" / fname).write_text(text, encoding="utf-8")

    return out


def _mw(value) -> str:
    return f"{value} mW" if value is not None else "unknown (not verifiable)"


def render_report(snapshot: DroneSnapshot, evaluation: Evaluation) -> str:
    fw = snapshot.firmware
    vtx = snapshot.vtx
    lines = [
        "drone-check report",
        "=" * 40,
        f"Captured   : {snapshot.captured_at}",
        f"Pilot_Name : {snapshot.pilot_name or '(none)'}",
        f"Craft_Name : {snapshot.craft_name or '(none)'}",
        f"FC serial  : {snapshot.uid}",
        "",
        f"Firmware : {fw.firmware_name} {fw.version} ({fw.variant})",
        f"Target   : {fw.target}  board: {fw.board_name}",
        f"Git hash : {fw.git_hash}  build: {fw.build_date} {fw.build_time}",
        f"Hash OK  : {snapshot.firmware_hash_approved} (via {snapshot.firmware_hash_source})",
        "",
        "VTX:",
        f"  device type        : {vtx.device_type}",
        f"  power table source : {vtx.power_table_source} (values are {vtx.power_unit})",
        f"  power verifiable   : {'yes' if vtx.power_verifiable else 'NO - cannot confirm real power'}",
        f"  low power on disarm: {vtx.low_power_disarm}",
        f"  armed max power    : {_mw(vtx.power_armed_max_mw)}",
        f"  disarmed power     : {_mw(vtx.power_disarmed_mw)}",
        f"  power switches     : {len(vtx.switches)}",
        f"  OSD label honest   : {'NO - MANIPULATED' if vtx.osd_power_mismatch else 'yes'}",
    ]
    for lvl in vtx.levels:
        claim = f'"{lvl.label}"' + (f" ({lvl.label_mw} mW)" if lvl.label_mw is not None else "")
        flag = "  <-- OSD UNDERSTATES REAL POWER" if lvl.understated else ""
        lines.append(
            f"    level {lvl.index}: value {lvl.raw_value} {vtx.power_unit}"
            f" = {_mw(lvl.real_mw)}, label {claim}{flag}"
        )
    lines += [
        "",
        f"VERDICT  : {'PASS' if evaluation.passed else 'FAIL'}",
        "-" * 40,
    ]
    for r in evaluation.results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{mark}] {r.rule_id} ({r.severity}): {r.description}")
        if r.detail and not r.passed:
            lines.append(f"         -> {r.detail}")
    return "\n".join(lines) + "\n"
