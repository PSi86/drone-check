"""Parse Betaflight / INAV CLI output into structured data.

The two inputs that matter are:

* the ``version`` line, e.g.::

      # Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (024f8e13d) MSP API: 1.46
      # INAV/MATEKF405 7.1.0 Apr 21 2024 / 13:25:29 (03a5c1922)

* the ``diff all`` (or ``dump``) body, which is a sequence of CLI statements:
  ``set <key> = <value>``, ``vtx ...``, ``vtxtable ...``, ``aux ...``,
  ``board_name ...`` and so on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import FirmwareInfo

# Matches both the Betaflight and INAV version header lines. The target group is
# the MCU for Betaflight ("STM32F405") and the board for INAV ("MATEKF405"); an
# optional "(S405)" board hint and an optional "MSP API: 1.46" suffix are kept
# out of the captured groups.
_VERSION_RE = re.compile(
    r"""^\#?\s*
        (?P<fw>[A-Za-z]+)\s*/\s*
        (?P<target>[^\s(]+)
        (?:\s*\([^)]*\))?\s+
        (?P<version>\d+\.\d+\.\d+\S*)\s+
        (?P<date>[A-Za-z]{3}\s+\d{1,2}\s+\d{4})\s*/\s*
        (?P<time>\d{2}:\d{2}:\d{2})\s*
        \((?P<hash>[0-9a-fA-F]+)\)
        (?:\s*MSP\s*API:\s*(?P<msp>[\d.]+))?
    """,
    re.VERBOSE,
)

_FW_NAMES = {
    "betaflight": ("BTFL", "Betaflight"),
    "inav": ("INAV", "INAV"),
    "emuflight": ("EMUF", "EmuFlight"),
}


@dataclass
class ParsedConfig:
    """Structured view of a ``diff all`` / ``dump`` body."""

    settings: dict[str, str] = field(default_factory=dict)
    vtx_lines: list[list[int]] = field(default_factory=list)  # numeric vtx control args
    aux_lines: list[list[int]] = field(default_factory=list)
    logic_lines: list[list[int]] = field(default_factory=list)  # INAV programming framework
    vtxtable: dict[str, list] = field(default_factory=dict)  # e.g. {"powervalues": [...]}
    board_name: str = ""
    manufacturer_id: str = ""


def parse_version_line(text: str) -> FirmwareInfo:
    """Parse the firmware ``version`` line; tolerant of surrounding noise."""
    info = FirmwareInfo()
    for line in text.splitlines():
        m = _VERSION_RE.match(line.strip())
        if not m:
            continue
        fw = m.group("fw")
        variant, name = _FW_NAMES.get(fw.lower(), (fw.upper()[:4], fw))
        info.variant = variant
        info.firmware_name = name
        info.target = m.group("target")
        info.version = m.group("version")
        info.build_date = m.group("date")
        info.build_time = m.group("time")
        info.git_hash = m.group("hash").lower()
        info.msp_api = m.group("msp") or ""
        return info
    return info


def _as_ints(tokens: list[str]) -> list[int]:
    out: list[int] = []
    for t in tokens:
        try:
            out.append(int(t))
        except ValueError:
            return []  # not a purely numeric line; caller will ignore it
    return out


def parse_diff(text: str) -> ParsedConfig:
    """Parse a ``diff all`` / ``dump`` body into a :class:`ParsedConfig`."""
    cfg = ParsedConfig()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("set "):
            body = line[4:]
            if "=" in body:
                key, _, value = body.partition("=")
                cfg.settings[key.strip()] = value.strip()
            continue

        head, _, rest = line.partition(" ")
        tokens = rest.split()

        if head == "vtx":
            nums = _as_ints(tokens)
            if len(nums) >= 6:
                cfg.vtx_lines.append(nums)
        elif head == "aux":
            nums = _as_ints(tokens)
            if len(nums) >= 5:
                cfg.aux_lines.append(nums)
        elif head == "logic":
            # INAV: logic <rule> <enabled> <activatorId> <operation>
            #       <opA_type> <opA_value> <opB_type> <opB_value> <flags>
            nums = _as_ints(tokens)
            if len(nums) >= 8:
                cfg.logic_lines.append(nums)
        elif head == "vtxtable":
            # e.g. "vtxtable powervalues 14 20 26 36"  (numeric)
            #      "vtxtable powerlabels 25 100 400 MAX"  (labels are strings!)
            # Keep numeric tokens as ints and non-numeric (e.g. "MAX") as str so
            # display labels are never silently dropped.
            if len(tokens) >= 2:
                sub = tokens[0]
                parsed: list = []
                for t in tokens[1:]:
                    try:
                        parsed.append(int(t))
                    except ValueError:
                        parsed.append(t)
                cfg.vtxtable[sub] = parsed
        elif head == "board_name" and tokens:
            cfg.board_name = tokens[0]
        elif head == "manufacturer_id" and tokens:
            cfg.manufacturer_id = tokens[0]

    return cfg
