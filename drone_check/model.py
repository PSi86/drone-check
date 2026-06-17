"""Data model for a captured drone.

Everything the tool produces for one flight controller flows into a single
:class:`DroneSnapshot`. The snapshot is JSON-serialisable (``to_dict``) and is
also the object the CEL rules are evaluated against, exposed under the ``drone``
binding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class FirmwareInfo:
    """Firmware identity, gathered from MSP and/or the CLI ``version`` line."""

    # "BTFL", "INAV", "EMUF", ... as reported by MSP_FC_VARIANT.
    variant: str = ""
    # Human-readable firmware name, e.g. "Betaflight" / "INAV".
    firmware_name: str = ""
    version: str = ""
    # MCU / target string from the version line, e.g. "STM32F405".
    target: str = ""
    board_name: str = ""
    manufacturer_id: str = ""
    build_date: str = ""
    build_time: str = ""
    # Short git revision of the firmware source commit, e.g. "024f8e13d".
    git_hash: str = ""
    msp_api: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VtxSwitch:
    """A radio switch (AUX channel) that can select a VTX power level.

    Derived from a Betaflight ``vtx`` control line. ``reachable_mw`` is the list
    of distinct output powers (milliwatt) this switch can select.
    """

    aux_channel: int  # zero-based: 0 == AUX1
    pwm_start: int
    pwm_end: int
    power_index: int
    reachable_mw: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VtxConfig:
    """Normalised VTX configuration relevant to power inspection."""

    # index -> milliwatt, resolved from `vtxtable powervalues` when present.
    power_table: dict[int, int] = field(default_factory=dict)
    # How power_table was resolved: "vtxtable" or "default-fallback".
    power_table_source: str = "unknown"

    low_power_disarm: str = "OFF"  # OFF | ON | UNTIL_FIRST_ARM
    power_global_index: Optional[int] = None
    power_global_mw: Optional[int] = None

    # Maximum power (mW) selectable while armed, considering the global setting
    # and every switch position. None when it cannot be resolved.
    power_armed_max_mw: Optional[int] = None
    # Effective power (mW) while disarmed.
    power_disarmed_mw: Optional[int] = None

    switches: list[VtxSwitch] = field(default_factory=list)
    # AUX channels (zero-based) that can toggle VTX PIT mode, best-effort.
    pit_mode_aux_channels: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # JSON object keys must be strings.
        d["power_table"] = {str(k): v for k, v in self.power_table.items()}
        return d


@dataclass
class CheckResult:
    """Outcome of a single rule evaluation."""

    rule_id: str
    description: str
    severity: str  # "critical" | "warning"
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Evaluation:
    """Aggregate verdict over all rules."""

    passed: bool = False
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failed_rules(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class DroneSnapshot:
    """Everything captured and derived for one flight controller."""

    captured_at: str = ""  # ISO-8601 timestamp, injected by the caller
    pilot: str = ""
    # 96-bit MCU unique id as hex, used as the flight-controller serial.
    uid: str = ""

    firmware: FirmwareInfo = field(default_factory=FirmwareInfo)
    vtx: VtxConfig = field(default_factory=VtxConfig)

    # Flat `set name = value` map from `diff all` / `dump`.
    settings: dict[str, str] = field(default_factory=dict)
    # Raw text of each captured CLI command, keyed by command.
    raw_cli: dict[str, str] = field(default_factory=dict)

    # Firmware-hash check result, filled by firmware.py before rule evaluation.
    firmware_hash_approved: bool = False
    firmware_hash_source: str = ""  # "allowlist" | "github" | "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "pilot": self.pilot,
            "uid": self.uid,
            "firmware": self.firmware.to_dict(),
            "vtx": self.vtx.to_dict(),
            "settings": self.settings,
            "firmware_hash_approved": self.firmware_hash_approved,
            "firmware_hash_source": self.firmware_hash_source,
        }

    def to_cel_context(self) -> dict[str, Any]:
        """Build the activation object exposed to CEL rules.

        Exposed bindings:
          * ``drone``  – firmware + normalised vtx + raw settings
          * ``checks`` – pre-computed boolean check results
        """
        return {
            "drone": {
                "pilot": self.pilot,
                "uid": self.uid,
                "firmware": self.firmware.to_dict(),
                "vtx": self.vtx.to_dict(),
                "settings": self.settings,
            },
            "checks": {
                "firmware_hash_approved": self.firmware_hash_approved,
            },
        }
