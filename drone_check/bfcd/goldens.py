"""Compare MSP responses against a reference, ignoring dynamic fields (BFCD-009).

SITL stays the reference oracle: the same dump is loaded into SITL and into
bf-configd, the same commands are sent, and the raw payloads are compared. But
not every byte may match — uptime, CPU load, sensor/arming flags and battery
runtime legitimately differ between two processes. So each command carries a
*comparison mask* (``config/bfcd_msp_masks.yaml``): ``exact`` for fully
deterministic commands, or ``masked`` with the byte ranges to blank before
comparing.

This module is pure data-in/data-out so the comparison logic is unit-testable
without ever starting SITL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CompareMask:
    """How to compare one command's payload."""

    mode: str = "exact"                 # "exact" or "masked"
    ignore_bytes: list[list[int]] = field(default_factory=list)  # [[start, end), ...]


@dataclass
class CompareResult:
    equal: bool
    detail: str = ""


_DEFAULT_MASKS_NAME = "bfcd_msp_masks.yaml"


def load_masks(config_dir: Path) -> dict[str, CompareMask]:
    """Load per-command comparison masks; returns ``{}`` if the file is absent."""
    path = Path(config_dir) / _DEFAULT_MASKS_NAME
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    masks: dict[str, CompareMask] = {}
    for name, spec in (doc.items() if isinstance(doc, dict) else []):
        spec = spec or {}
        ranges = []
        for r in spec.get("ignore_bytes", []) or []:
            if isinstance(r, (list, tuple)) and len(r) == 2:
                ranges.append([int(r[0]), int(r[1])])
        masks[name] = CompareMask(mode=str(spec.get("compare", "exact")),
                                  ignore_bytes=ranges)
    return masks


def _blank(payload: bytes, ignore_bytes: list[list[int]]) -> bytearray:
    """Return a copy of ``payload`` with the ignored byte ranges zeroed."""
    out = bytearray(payload)
    for start, end in ignore_bytes:
        for i in range(max(0, start), min(len(out), end)):
            out[i] = 0
    return out


def compare_payload(expected: bytes, actual: bytes,
                    mask: CompareMask | None = None) -> CompareResult:
    """Compare two MSP payloads under ``mask`` (default: exact).

    A length mismatch is always a difference — even fully-masked commands must
    agree on their structure. For ``masked`` mode the ignored ranges are zeroed
    in both buffers before the byte comparison, so a difference there is ignored
    but a difference anywhere else is still caught.
    """
    mask = mask or CompareMask()
    if len(expected) != len(actual):
        return CompareResult(False,
                             f"length differs: expected {len(expected)}, got {len(actual)}")
    if mask.mode == "masked":
        exp = _blank(expected, mask.ignore_bytes)
        act = _blank(actual, mask.ignore_bytes)
    else:
        exp, act = bytearray(expected), bytearray(actual)
    if exp == act:
        return CompareResult(True)
    diffs = [i for i in range(len(exp)) if exp[i] != act[i]]
    preview = ", ".join(str(i) for i in diffs[:8])
    more = "" if len(diffs) <= 8 else f" (+{len(diffs) - 8} more)"
    return CompareResult(False, f"{len(diffs)} byte(s) differ at offsets: {preview}{more}")
