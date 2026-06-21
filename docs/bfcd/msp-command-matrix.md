# MSP command matrix (BFCD-008)

How bf-configd answers each MSP command in read-only snapshot mode. The **source
of truth is `drone_check/bfcd/commands.py`** (`COMMANDS`); this table is
generated from it — regenerate after editing the table:

```bash
python -c "from drone_check.bfcd.commands import COMMANDS, Priority
for p in (Priority.A, Priority.B, Priority.C):
    [print(c.name, c.code, c.handling.value) for c in COMMANDS if c.priority is p]"
```

Handling:

- **exact** — answered straight from the real Betaflight config/PG structures.
- **synthetic** — real config structure with synthetic runtime fields (uptime,
  cpu load, sensor/arming flags, battery, motor values).
- **stubbed** — no real data; a stable, safe canned answer.
- **blocked** — a write or dangerous op; refused in read-only mode (logged).
- **unsupported** — not answered in the MVP.

Priority follows the plan §12: **A** = connection + base identity (without these
the Configurator will not attach), **B** = configuration tabs, **C** =
specialised tabs.

### Priority A

| Command | Code | Handling | Note |
|---|---:|---|---|
| `MSP_API_VERSION` | 1 | exact |  |
| `MSP_FC_VARIANT` | 2 | exact |  |
| `MSP_FC_VERSION` | 3 | exact |  |
| `MSP_BOARD_INFO` | 4 | synthetic | board id from target/dump; runtime sensor flags synthesised |
| `MSP_BUILD_INFO` | 5 | exact |  |
| `MSP_NAME` | 10 | exact |  |
| `MSP_STATUS` | 101 | synthetic | cycle time / cpu load / arming flags synthesised |
| `MSP_STATUS_EX` | 150 | synthetic |  |
| `MSP_FEATURE_CONFIG` | 36 | exact |  |
| `MSP_EEPROM_WRITE` | 250 | blocked | would persist |
| `MSP_REBOOT` | 68 | blocked |  |

### Priority B

| Command | Code | Handling | Note |
|---|---:|---|---|
| `MSP_CF_SERIAL_CONFIG` | 54 | exact | Ports tab |
| `MSP_BATTERY_CONFIG` | 32 | exact |  |
| `MSP_BATTERY_STATE` | 130 | synthetic | 0 V / disarmed synthetic battery state |
| `MSP_PID` | 112 | exact |  |
| `MSP_PID_ADVANCED` | 94 | exact |  |
| `MSP_RC_TUNING` | 111 | exact | Rates tab |
| `MSP_RC_DEADBAND` | 125 | exact |  |
| `MSP_RX_CONFIG` | 44 | exact | Receiver tab |
| `MSP_RX_MAP` | 64 | exact |  |
| `MSP_MODE_RANGES` | 34 | exact | Modes tab |
| `MSP_MODE_RANGES_EXTRA` | 238 | exact |  |
| `MSP_OSD_CONFIG` | 84 | exact | OSD tab |
| `MSP_VTX_CONFIG` | 88 | synthetic | config from dump; no live VTX device communication |
| `MSP_VTXTABLE_BAND` | 137 | exact | VTX Table tab |
| `MSP_VTXTABLE_POWERLEVEL` | 138 | exact |  |
| `MSP_MOTOR` | 104 | synthetic | 0 / disarmed |
| `MSP_MOTOR_CONFIG` | 131 | exact |  |
| `MSP_MIXER_CONFIG` | 42 | exact |  |

### Priority C

| Command | Code | Handling | Note |
|---|---:|---|---|
| `MSP_LED_STRIP_CONFIG` | 48 | exact |  |
| `MSP_LED_COLORS` | 46 | exact |  |
| `MSP_GPS_RESCUE` | 135 | exact |  |
| `MSP_BLACKBOX_CONFIG` | 80 | synthetic | config from dump; device reported as not present |
| `MSP_FAILSAFE_CONFIG` | 75 | exact |  |
| `MSP_SERVO_CONFIGURATIONS` | 120 | exact |  |
| `MSP_FILTER_CONFIG` | 92 | exact |  |
| `MSP_SET_RAW_RC` | 200 | blocked |  |
| `MSP_SET_MOTOR` | 214 | blocked |  |

> Note: codes and payload layouts are MSP-API-version-sensitive. This matrix is
> the MVP shape for the Betaflight 4.5 family; revisit per family when adding it
> (see `compatibility.md`).
