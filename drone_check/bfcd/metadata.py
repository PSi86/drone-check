"""Detect a dump's firmware family, target and MSP API (work package BFCD-002).

The native bf-configd backend is built and selected *per Betaflight firmware
family*, so before anything else we must read the dump's identity. drone-check
already parses the Betaflight/INAV ``version`` header and the ``board_name`` /
``manufacturer_id`` lines, so this module reuses :mod:`drone_check.parser`
rather than re-implementing that parsing — it only adds the bf-configd-specific
notions of *firmware family* (the build axis) and *metadata warnings* (so an
incomplete or non-Betaflight dump fails closed instead of being mis-served).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..parser import parse_diff, parse_version_line

# bf-configd targets Betaflight only; MSP_FC_VARIANT reports "BTFL".
BETAFLIGHT_VARIANT = "BTFL"


def firmware_family(version: str) -> str:
    """Reduce a firmware version to the family a backend is built for.

    The family is the build axis of the compatibility matrix: every patch level
    of a minor line shares one backend candidate.

    * Semantic Betaflight versions reduce to ``MAJOR.MINOR`` — ``"4.5.3"`` -> ``"4.5"``.
    * Date-based versions reduce to ``YEAR.MONTH`` — ``"2025.12.1"`` -> ``"2025.12"``.

    Both fall out of "join the first two dot-separated components", so a single
    rule covers the old and new Betaflight versioning schemes. Returns ``""`` for
    an unparseable version.
    """
    parts = version.strip().split(".")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return ""
    return f"{parts[0]}.{parts[1]}"


@dataclass
class DumpMetadata:
    """Identity extracted from a ``dump all``, plus any quality warnings.

    ``warnings`` collects everything that makes the dump harder (or unsafe) to
    serve faithfully — missing version, a non-Betaflight variant, an absent
    target. Callers should surface these rather than silently serving a
    best-effort guess.
    """

    firmware_name: str = ""          # "Betaflight", "INAV", ...
    variant: str = ""                # MSP_FC_VARIANT code, e.g. "BTFL"
    version: str = ""                # "4.5.3"
    firmware_family: str = ""        # "4.5"
    target: str = ""                 # MCU/target from the version line, e.g. "STM32F405"
    board_name: str = ""             # e.g. "SPEEDYBEEF405"
    manufacturer_id: str = ""
    msp_api: str = ""                # "1.46"
    git_hash: str = ""
    build_date: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_betaflight(self) -> bool:
        return self.variant == BETAFLIGHT_VARIANT

    @property
    def has_identity(self) -> bool:
        """True when the minimum needed to *pick a backend* is present."""
        return bool(self.firmware_family) and self.is_betaflight

    def to_dict(self) -> dict:
        return {
            "firmware_name": self.firmware_name,
            "variant": self.variant,
            "version": self.version,
            "firmware_family": self.firmware_family,
            "target": self.target,
            "board_name": self.board_name,
            "manufacturer_id": self.manufacturer_id,
            "msp_api": self.msp_api,
            "git_hash": self.git_hash,
            "build_date": self.build_date,
            "warnings": list(self.warnings),
        }


def detect_metadata(dump_text: str) -> DumpMetadata:
    """Extract bf-configd metadata from a ``dump all`` (or ``diff all``) body.

    Reuses drone-check's existing parsing and layers on the family derivation
    and the warning system. Never raises on a malformed dump — an empty/garbage
    input yields a :class:`DumpMetadata` whose ``warnings`` explain why it cannot
    be served.
    """
    md = DumpMetadata()

    info = parse_version_line(dump_text)
    md.firmware_name = info.firmware_name
    md.variant = info.variant
    md.version = info.version
    md.target = info.target
    md.msp_api = info.msp_api
    md.git_hash = info.git_hash
    md.build_date = info.build_date

    # board_name / manufacturer_id live in the body, not the header.
    cfg = parse_diff(dump_text)
    md.board_name = cfg.board_name
    md.manufacturer_id = cfg.manufacturer_id

    md.firmware_family = firmware_family(md.version)

    if not md.version:
        md.warnings.append("no firmware version header found in dump")
    elif not md.firmware_family:
        md.warnings.append(f"could not derive a firmware family from version {md.version!r}")

    if md.variant and not md.is_betaflight:
        md.warnings.append(
            f"firmware variant {md.variant!r} is not Betaflight; bf-configd only "
            f"emulates Betaflight"
        )
    elif not md.variant:
        md.warnings.append("could not identify the firmware variant")

    if not md.target:
        md.warnings.append("no target/MCU found in version line; a generic target "
                           "context will be used (best effort)")
    if not md.msp_api:
        md.warnings.append("no MSP API version found in version line")

    return md
