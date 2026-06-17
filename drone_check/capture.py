"""Assemble a :class:`DroneSnapshot` from MSP identity + captured CLI output.

This is the glue between the transport/identification layers and the
parser/VTX layers. It does not perform I/O itself.
"""

from __future__ import annotations

from .model import DroneSnapshot, FirmwareInfo
from .msp import MspIdentity
from .parser import parse_diff, parse_version_line
from .vtx import normalise_vtx

# Authoritative settings source: `dump all` (every setting, absolute values).
_DUMP_COMMANDS = ("dump all", "dump")
# Only used when diff fallback is explicitly enabled.
_DIFF_COMMANDS = ("diff all", "diff")


def _merge_firmware(ident: MspIdentity, version_line: FirmwareInfo) -> FirmwareInfo:
    """Combine MSP identity with the parsed `version` line (CLI wins on text)."""
    fw = FirmwareInfo(
        variant=version_line.variant or ident.variant,
        firmware_name=version_line.firmware_name,
        version=version_line.version or ident.version,
        target=version_line.target,
        board_name=version_line.board_name or ident.board_name,
        build_date=version_line.build_date or ident.build_date,
        build_time=version_line.build_time or ident.build_time,
        git_hash=version_line.git_hash or ident.git_hash,
        msp_api=version_line.msp_api or ident.api_version,
    )
    if not fw.firmware_name:
        fw.firmware_name = {"BTFL": "Betaflight", "INAV": "INAV"}.get(fw.variant, fw.variant)
    return fw


def build_snapshot(
    ident: MspIdentity,
    cli_outputs: dict[str, str],
    captured_at: str = "",
    allow_diff_fallback: bool = False,
) -> DroneSnapshot:
    """Build a snapshot (without pilot / hash result, which are filled later).

    The settings are read from ``dump all`` (authoritative, absolute values).
    Falling back to ``diff all``/``diff`` only happens when explicitly enabled,
    because a diff omits default-valued settings and would make rule evaluation
    depend on guessed defaults.
    """
    snap = DroneSnapshot(captured_at=captured_at, uid=ident.uid)
    snap.raw_cli = dict(cli_outputs)

    version_text = cli_outputs.get("version", "")
    version_info = parse_version_line(version_text)

    # Pick the settings source: dump first, optionally diff as a fallback.
    sources = list(_DUMP_COMMANDS)
    if allow_diff_fallback:
        sources += list(_DIFF_COMMANDS)
    dump_text = ""
    for cmd in sources:
        if cli_outputs.get(cmd):
            dump_text = cli_outputs[cmd]
            break

    # The version header also appears inside the dump; use it as a fallback.
    if not version_info.git_hash and dump_text:
        version_info = parse_version_line(dump_text)

    cfg = parse_diff(dump_text)
    snap.firmware = _merge_firmware(ident, version_info)
    # The CLI board_name line is authoritative; MSP board info is only a fallback.
    if cfg.board_name:
        snap.firmware.board_name = cfg.board_name
    if cfg.manufacturer_id:
        snap.firmware.manufacturer_id = cfg.manufacturer_id

    snap.settings = cfg.settings
    snap.vtx = normalise_vtx(cfg, snap.firmware.variant)
    return snap
