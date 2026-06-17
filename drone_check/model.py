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
class VtxPowerLevel:
    """One ``vtxtable`` power level, with the real power decoded from its value.

    ``raw_value`` is the number sent to the VTX (dBm for SmartAudio 2.1, mW for
    IRC Tramp). ``real_mw`` is the decoded true output power. ``label`` is the
    free-form OSD string and ``label_mw`` its parsed mW claim. ``understated`` is
    True when the OSD label claims meaningfully less than the real power — the
    classic "show 25 mW while transmitting more" manipulation.
    """

    index: int  # 1-based vtxtable index
    raw_value: int
    # Real output power in mW; None when it cannot be derived from the value
    # (SmartAudio V1/V2 index-based tables — the value is an opaque index).
    real_mw: Optional[int] = None
    label: str = ""
    label_mw: Optional[int] = None
    understated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VtxConfig:
    """Normalised VTX configuration relevant to power inspection."""

    # index -> real output power in milliwatt (dBm-decoded where applicable).
    # Values are None for index-based tables where mW cannot be derived.
    power_table: dict[int, Optional[int]] = field(default_factory=dict)
    # How power_table was resolved: "vtxtable" or "default-fallback".
    power_table_source: str = "unknown"
    # VTX device type from MSP_VTX_CONFIG: "SmartAudio"/"Tramp"/"RTC6705"/"MSP"/"unknown".
    device_type: str = "unknown"
    # How the FC encodes powervalues: "dbm" (SA 2.1) | "mw" (Tramp) |
    # "index" (SA V1/V2, opaque) | "unknown".
    power_unit: str = "mw"
    # True only when the real power can be derived from the FC config (dbm/mw).
    # False for index-based tables — the real power then cannot be verified.
    power_verifiable: bool = True

    low_power_disarm: str = "OFF"  # OFF | ON | UNTIL_FIRST_ARM
    power_global_index: Optional[int] = None
    power_global_mw: Optional[int] = None

    # Maximum power (mW) selectable while armed, considering the global setting
    # and every switch position. None when it cannot be resolved.
    power_armed_max_mw: Optional[int] = None
    # Effective power (mW) while disarmed.
    power_disarmed_mw: Optional[int] = None

    # Per-level decode + OSD-label honesty check.
    levels: list[VtxPowerLevel] = field(default_factory=list)
    # True if ANY level's OSD label understates the real power (manipulation).
    osd_power_mismatch: bool = False

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
    # 96-bit MCU unique id as hex, used as the flight-controller serial.
    uid: str = ""

    # Identity read from the flight controller itself — the single source of
    # truth. `pilot_name`/`craft_name` (Betaflight >= 4.3, INAV); on older
    # Betaflight these come from `display_name`/`name`, on INAV craft is `name`.
    pilot_name: str = ""
    craft_name: str = ""

    firmware: FirmwareInfo = field(default_factory=FirmwareInfo)
    vtx: VtxConfig = field(default_factory=VtxConfig)

    # Flat `set name = value` map from the `dump` output.
    settings: dict[str, str] = field(default_factory=dict)
    # Raw text of each captured CLI command, keyed by command.
    raw_cli: dict[str, str] = field(default_factory=dict)

    # Firmware-hash check result, filled by firmware.py before rule evaluation.
    firmware_hash_approved: bool = False
    firmware_hash_source: str = ""  # "allowlist" | "github" | "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "uid": self.uid,
            "pilot_name": self.pilot_name,
            "craft_name": self.craft_name,
            "firmware": self.firmware.to_dict(),
            "vtx": self.vtx.to_dict(),
            "settings": self.settings,
            "firmware_hash_approved": self.firmware_hash_approved,
            "firmware_hash_source": self.firmware_hash_source,
        }

    def to_cel_context(self) -> dict[str, Any]:
        """Build the activation object exposed to CEL rules.

        Exposed bindings:
          * ``drone``  – firmware + normalised vtx + raw settings + names
          * ``checks`` – pre-computed boolean check results
        """
        return {
            "drone": {
                "uid": self.uid,
                "pilot_name": self.pilot_name,
                "craft_name": self.craft_name,
                "firmware": self.firmware.to_dict(),
                "vtx": self.vtx.to_dict(),
                "settings": self.settings,
            },
            "checks": {
                "firmware_hash_approved": self.firmware_hash_approved,
            },
        }
