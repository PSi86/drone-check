"""The MSP command matrix as data (BFCD-008).

Every command bf-configd cares about is listed here once, with its id, the
Configurator priority that needs it, and how bf-configd intends to answer it.
This single table drives three things so they cannot drift apart:

* the human-readable ``docs/bfcd/msp-command-matrix.md`` (generated from here),
* the default probe command lists per priority (golden tests / bring-up),
* a compile-time-ish sanity check that ids are unique (a unit test).

Ids and the priority groupings come from Betaflight ``src/main/msp/msp_protocol.h``
and the plan's §12. Only commands relevant to a read-only config snapshot are
listed; this is intentionally not the full MSP surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Priority(str, Enum):
    """Configurator-connect priority from the plan §12."""

    A = "A"  # connection + base identity; without these the app won't attach
    B = "B"  # configuration tabs (ports, PID, rates, modes, OSD, VTX, ...)
    C = "C"  # specialised tabs (LED, GPS rescue, blackbox, servos, filters)


class Handling(str, Enum):
    """How bf-configd answers a command in read-only snapshot mode."""

    EXACT = "exact"          # straight from real config/PG structures
    SYNTHETIC = "synthetic"  # real structure + synthetic runtime fields
    STUBBED = "stubbed"      # no real data; a stable, safe canned answer
    BLOCKED = "blocked"      # a write/dangerous op — refused in read-only mode
    UNSUPPORTED = "unsupported"  # not answered in the MVP


@dataclass(frozen=True)
class MspCommand:
    name: str
    code: int
    priority: Priority
    handling: Handling
    note: str = ""


# Ordered by priority then code. Keep names matching msp_protocol.h.
COMMANDS: tuple[MspCommand, ...] = (
    # -- Priority A: connection & base identity -------------------------------
    MspCommand("MSP_API_VERSION", 1, Priority.A, Handling.EXACT),
    MspCommand("MSP_FC_VARIANT", 2, Priority.A, Handling.EXACT),
    MspCommand("MSP_FC_VERSION", 3, Priority.A, Handling.EXACT),
    MspCommand("MSP_BOARD_INFO", 4, Priority.A, Handling.SYNTHETIC,
               "board id from target/dump; runtime sensor flags synthesised"),
    MspCommand("MSP_BUILD_INFO", 5, Priority.A, Handling.EXACT),
    MspCommand("MSP_NAME", 10, Priority.A, Handling.EXACT),
    MspCommand("MSP_STATUS", 101, Priority.A, Handling.SYNTHETIC,
               "cycle time / cpu load / arming flags synthesised"),
    MspCommand("MSP_STATUS_EX", 150, Priority.A, Handling.SYNTHETIC),
    MspCommand("MSP_FEATURE_CONFIG", 36, Priority.A, Handling.EXACT),
    # -- Priority B: configuration tabs ---------------------------------------
    MspCommand("MSP_CF_SERIAL_CONFIG", 54, Priority.B, Handling.EXACT, "Ports tab"),
    MspCommand("MSP_BATTERY_CONFIG", 32, Priority.B, Handling.EXACT),
    MspCommand("MSP_BATTERY_STATE", 130, Priority.B, Handling.SYNTHETIC,
               "0 V / disarmed synthetic battery state"),
    MspCommand("MSP_PID", 112, Priority.B, Handling.EXACT),
    MspCommand("MSP_PID_ADVANCED", 94, Priority.B, Handling.EXACT),
    MspCommand("MSP_RC_TUNING", 111, Priority.B, Handling.EXACT, "Rates tab"),
    MspCommand("MSP_RC_DEADBAND", 125, Priority.B, Handling.EXACT),
    MspCommand("MSP_RX_CONFIG", 44, Priority.B, Handling.EXACT, "Receiver tab"),
    MspCommand("MSP_RX_MAP", 64, Priority.B, Handling.EXACT),
    MspCommand("MSP_MODE_RANGES", 34, Priority.B, Handling.EXACT, "Modes tab"),
    MspCommand("MSP_MODE_RANGES_EXTRA", 238, Priority.B, Handling.EXACT),
    MspCommand("MSP_OSD_CONFIG", 84, Priority.B, Handling.EXACT, "OSD tab"),
    MspCommand("MSP_VTX_CONFIG", 88, Priority.B, Handling.SYNTHETIC,
               "config from dump; no live VTX device communication"),
    MspCommand("MSP_VTXTABLE_BAND", 137, Priority.B, Handling.EXACT, "VTX Table tab"),
    MspCommand("MSP_VTXTABLE_POWERLEVEL", 138, Priority.B, Handling.EXACT),
    MspCommand("MSP_MOTOR", 104, Priority.B, Handling.SYNTHETIC, "0 / disarmed"),
    MspCommand("MSP_MOTOR_CONFIG", 131, Priority.B, Handling.EXACT),
    MspCommand("MSP_MIXER_CONFIG", 42, Priority.B, Handling.EXACT),
    # -- Priority C: specialised tabs -----------------------------------------
    MspCommand("MSP_LED_STRIP_CONFIG", 48, Priority.C, Handling.EXACT),
    MspCommand("MSP_LED_COLORS", 46, Priority.C, Handling.EXACT),
    MspCommand("MSP_GPS_RESCUE", 135, Priority.C, Handling.EXACT),
    MspCommand("MSP_BLACKBOX_CONFIG", 80, Priority.C, Handling.SYNTHETIC,
               "config from dump; device reported as not present"),
    MspCommand("MSP_FAILSAFE_CONFIG", 75, Priority.C, Handling.EXACT),
    MspCommand("MSP_SERVO_CONFIGURATIONS", 120, Priority.C, Handling.EXACT),
    MspCommand("MSP_FILTER_CONFIG", 92, Priority.C, Handling.EXACT),
    # -- Writes / dangerous ops — refused in read-only mode -------------------
    MspCommand("MSP_SET_RAW_RC", 200, Priority.C, Handling.BLOCKED),
    MspCommand("MSP_EEPROM_WRITE", 250, Priority.A, Handling.BLOCKED, "would persist"),
    MspCommand("MSP_SET_MOTOR", 214, Priority.C, Handling.BLOCKED),
    MspCommand("MSP_REBOOT", 68, Priority.A, Handling.BLOCKED),
)

# Name -> command, for lookups and doc generation.
BY_NAME: dict[str, MspCommand] = {c.name: c for c in COMMANDS}
BY_CODE: dict[int, MspCommand] = {c.code: c for c in COMMANDS}


def commands_for_priority(priority: Priority) -> list[MspCommand]:
    """The read (non-blocked) commands for a priority — a default probe list."""
    return [c for c in COMMANDS
            if c.priority is priority and c.handling is not Handling.BLOCKED]
