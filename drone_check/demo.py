"""Canned flight-controller profiles for offline demos and tests.

These let the whole pipeline run (and the web UI render verdicts) without any
hardware attached. The Betaflight sample is intentionally *non-compliant* (a
switch can select 200 mW) and the INAV sample is compliant, so both the red and
green paths are exercised. The sample bodies are trimmed but stand in for a full
``dump all`` (every value explicit), which is what the tool captures.
"""

from __future__ import annotations

from .flightcontroller import FcProfile
from .msp import MspIdentity

BETAFLIGHT_DUMP = """\
# version
# Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (77d01ba3b) MSP API: 1.46

# start the command batch
batch start

board_name HBFCS405
manufacturer_id NEUT

# feature
feature -RX_PARALLEL_PWM
feature RSSI_ADC

# serial
serial 0 64 115200 57600 0 115200

# aux
aux 0 0 0 1700 2100 0 0
aux 1 13 1 900 1300 0 0

# vtxtable
vtxtable bands 6
vtxtable channels 8
vtxtable powervalues 25 100 200 400 600
vtxtable powerlabels 25 100 200 400 600

# vtx
vtx 0 2 0 0 1 900 1200
vtx 1 2 0 0 2 1300 1700
vtx 2 2 0 0 3 1800 2100

# master
set craft_name = TESTQUAD
set pilot_name = MAX POWER
set vtx_band = 5
set vtx_channel = 1
set vtx_power = 1
set vtx_low_power_disarm = ON
set vtx_freq = 5917

profile 0

# end the command batch
batch end
"""

BETAFLIGHT_STATUS = """\
MCU F405 Clock=168MHz, Vref=3.26V, Core temp=42degC
System Uptime: 12 seconds, Current Time: 2024-12-19T12:34:56.000+00:00
CPU:18%, cycle time: 125, GYRO rate: 8000, RX rate: 15, System rate: 10
"""

INAV_DUMP = """\
# version
# INAV/MATEKF405 7.1.0 Apr 21 2024 / 13:25:29 (aa8543654)

# resources

# vtxtable
vtxtable powerlevels 5
vtxtable powervalues 25 100 200 400 600
vtxtable powerlabels 25 100 200 400 600

# master
set name = WINGONE
set pilot_name = ANNA
set vtx_band = 5
set vtx_channel = 1
set vtx_power = 1
set vtx_low_power_disarm = ON

# end
"""


# INAV with VTX power placed on RC channel 6 via the programming framework
# (operation 25 = Set VTx Power Level, operand A type 1 = Get RC Channel).
INAV_DUMP_SWITCH = """\
# version
# INAV/MATEKF405 7.1.0 Apr 21 2024 / 13:25:29 (aa8543654)

# master
set vtx_band = 5
set vtx_channel = 1
set vtx_power = 1
set vtx_low_power_disarm = OFF

# logic
logic 0 1 -1 25 1 6 0 0 0

# end
"""


def betaflight_profile() -> FcProfile:
    ident = MspIdentity(
        api_version="1.46",
        variant="BTFL",
        version="4.5.1",
        board_name="HBFCS405",
        build_date="Dec 19 2024",
        build_time="12:34:56",
        git_hash="77d01ba3b",
        uid="0041002f3530510835353036",
        vtx_type=4,  # IRC Tramp (mW values)
    )
    return FcProfile(
        identity=ident,
        cli_outputs={
            "version": BETAFLIGHT_DUMP.splitlines()[1],
            "dump all": BETAFLIGHT_DUMP,
            "status": BETAFLIGHT_STATUS,
        },
    )


def inav_profile() -> FcProfile:
    ident = MspIdentity(
        api_version="2.5",
        variant="INAV",
        version="7.1.0",
        board_name="MATEKF405",
        build_date="Apr 21 2024",
        build_time="13:25:29",
        git_hash="aa8543654",
        uid="00450031511239363330303a",
    )
    return FcProfile(
        identity=ident,
        cli_outputs={
            "version": INAV_DUMP.splitlines()[1],
            "dump all": INAV_DUMP,
            "status": "",
        },
    )


# A SmartAudio 2.1 drone whose OSD labels LIE: every level is labelled "25"
# while the dBm values transmit 25 / 100 / 400 mW. vtx_power selects level 3
# (26 dBm = ~400 mW) but the OSD shows "25". This is the classic power cheat.
SMARTAUDIO_CHEAT_DUMP = """\
# version
# Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (77d01ba3b) MSP API: 1.46

# start the command batch
batch start

board_name HBFCS405

# vtxtable
vtxtable bands 6
vtxtable channels 8
vtxtable powervalues 14 20 26
vtxtable powerlabels 25 25 25

# master
set craft_name = RACER
set pilot_name = SNEAKY
set vtx_band = 5
set vtx_channel = 1
set vtx_power = 3
set vtx_low_power_disarm = OFF

# end the command batch
batch end
"""


def smartaudio_cheat_profile() -> FcProfile:
    ident = MspIdentity(
        api_version="1.46",
        variant="BTFL",
        version="4.5.1",
        board_name="HBFCS405",
        build_date="Dec 19 2024",
        build_time="12:34:56",
        git_hash="77d01ba3b",
        uid="00410033511239363330303b",
        vtx_type=3,  # SmartAudio (dBm values)
    )
    return FcProfile(
        identity=ident,
        cli_outputs={
            "version": SMARTAUDIO_CHEAT_DUMP.splitlines()[1],
            "dump all": SMARTAUDIO_CHEAT_DUMP,
            "status": "",
        },
    )


# A SmartAudio V2.0 drone: powervalues are opaque indices (0 1 2 3). The real
# power lives only in the device + the (manipulable) labels, so it cannot be
# verified from the FC config — the tool must say so instead of trusting "25".
SMARTAUDIO_INDEX_DUMP = """\
# version
# Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (77d01ba3b) MSP API: 1.46

batch start
board_name HBFCS405

# vtxtable
vtxtable powervalues 0 1 2 3
vtxtable powerlabels 25 200 500 800

# master
set craft_name = OLDQUAD
set pilot_name = LEGACY
set vtx_power = 1
set vtx_low_power_disarm = ON

batch end
"""


def smartaudio_index_profile() -> FcProfile:
    ident = MspIdentity(
        api_version="1.46",
        variant="BTFL",
        version="4.5.1",
        board_name="HBFCS405",
        build_date="Dec 19 2024",
        build_time="12:34:56",
        git_hash="77d01ba3b",
        uid="00410034511239363330303c",
        vtx_type=3,  # SmartAudio, but V2.0 index-based table
    )
    return FcProfile(
        identity=ident,
        cli_outputs={
            "version": SMARTAUDIO_INDEX_DUMP.splitlines()[1],
            "dump all": SMARTAUDIO_INDEX_DUMP,
            "status": "",
        },
    )


def demo_profiles() -> list[FcProfile]:
    return [
        betaflight_profile(),
        inav_profile(),
        smartaudio_cheat_profile(),
        smartaudio_index_profile(),
    ]


def seed_allowlist(allowlist: dict) -> None:
    """Add the demo firmware hashes so the demo passes offline.

    These are the real release tag commit prefixes, so the demo also validates
    against the generated allowlist without seeding.
    """
    allowlist.setdefault("BTFL", {}).setdefault("4.5.1", []).append("77d01ba3b")
    allowlist.setdefault("INAV", {}).setdefault("7.1.0", []).append("aa8543654")
