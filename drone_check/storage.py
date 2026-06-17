"""Persist captured data to disk.

Layout (per the project brief: organised by pilot name and by flight-controller
serial)::

    logs/<pilot>/<uid>/<timestamp>/
        snapshot.json     normalised DroneSnapshot
        evaluation.json   rule results + verdict
        report.txt        human-readable summary
        raw/<command>.txt  raw CLI output for every captured command
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .model import DroneSnapshot, Evaluation

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name: str, fallback: str) -> str:
    cleaned = _SAFE.sub("_", (name or "").strip()).strip("_")
    return cleaned or fallback


def save_capture(
    base_dir: Path,
    snapshot: DroneSnapshot,
    evaluation: Evaluation,
    raw_cli: dict[str, str],
    timestamp: str,
) -> Path:
    """Write all artefacts for one capture and return the created directory."""
    pilot = _safe(snapshot.pilot, "unknown_pilot")
    uid = _safe(snapshot.uid, "unknown_fc")
    stamp = _safe(timestamp, "capture")

    out = base_dir / pilot / uid / stamp
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


def render_report(snapshot: DroneSnapshot, evaluation: Evaluation) -> str:
    fw = snapshot.firmware
    vtx = snapshot.vtx
    lines = [
        "drone-check report",
        "=" * 40,
        f"Captured : {snapshot.captured_at}",
        f"Pilot    : {snapshot.pilot or '(unknown)'}",
        f"FC serial: {snapshot.uid}",
        "",
        f"Firmware : {fw.firmware_name} {fw.version} ({fw.variant})",
        f"Target   : {fw.target}  board: {fw.board_name}",
        f"Git hash : {fw.git_hash}  build: {fw.build_date} {fw.build_time}",
        f"Hash OK  : {snapshot.firmware_hash_approved} (via {snapshot.firmware_hash_source})",
        "",
        "VTX:",
        f"  power table source : {vtx.power_table_source}",
        f"  low power on disarm: {vtx.low_power_disarm}",
        f"  armed max power    : {vtx.power_armed_max_mw} mW",
        f"  disarmed power     : {vtx.power_disarmed_mw} mW",
        f"  power switches     : {len(vtx.switches)}",
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
