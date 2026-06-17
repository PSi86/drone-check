from drone_check.demo import (
    BETAFLIGHT_DUMP,
    INAV_DUMP,
    INAV_DUMP_SWITCH,
    SMARTAUDIO_INDEX_DUMP,
)
from drone_check.parser import parse_diff
from drone_check.vtx import dbm_to_mw, normalise_vtx, parse_label_mw

# A honest SmartAudio 2.1 table: powervalues are dBm, labels are the real mW.
_SA_HONEST = """\
# Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (77d01ba3b) MSP API: 1.46
vtxtable powervalues 14 20 26 36
vtxtable powerlabels 25 100 400 MAX
set vtx_power = 1
set vtx_low_power_disarm = ON
"""

# A cheat: dBm values transmit 25/100/400 mW but every label says "25".
_SA_CHEAT = """\
# Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (77d01ba3b) MSP API: 1.46
vtxtable powervalues 14 20 26
vtxtable powerlabels 25 25 25
set vtx_power = 3
set vtx_low_power_disarm = OFF
"""

# IRC Tramp: powervalues are mW directly, labels match.
_TRAMP = """\
# Betaflight / STM32F405 (S405) 4.5.1 Dec 19 2024 / 12:34:56 (77d01ba3b) MSP API: 1.46
vtxtable powervalues 25 100 200 400 600
vtxtable powerlabels 25 100 200 400 600
set vtx_power = 2
"""


def test_dbm_to_mw_and_label_parsing():
    assert dbm_to_mw(14) == 25 and dbm_to_mw(20) == 100 and dbm_to_mw(26) == 398
    assert parse_label_mw("25") == 25 and parse_label_mw("MAX") is None
    assert parse_label_mw("1W6") == 1600 and parse_label_mw("2W") == 2000


def test_smartaudio_dbm_decoded_not_taken_as_mw():
    vtx = normalise_vtx(parse_diff(_SA_HONEST))
    assert vtx.power_unit == "dbm"
    # 14 dBm is 25 mW, NOT 14 mW (the original bug)
    assert vtx.power_table[1] == 25
    assert vtx.power_table[2] == 100
    assert vtx.power_table[3] == 398
    assert vtx.power_global_mw == 25
    assert vtx.power_armed_max_mw == 25
    assert vtx.power_disarmed_mw == 25
    # honest labels -> no manipulation flag
    assert vtx.osd_power_mismatch is False


def test_osd_label_manipulation_detected():
    vtx = normalise_vtx(parse_diff(_SA_CHEAT))
    assert vtx.power_unit == "dbm"
    assert vtx.osd_power_mismatch is True
    # the OSD shows "25" but vtx_power = 3 = 26 dBm = ~400 mW
    assert vtx.power_armed_max_mw == 398
    understated = {lvl.index for lvl in vtx.levels if lvl.understated}
    assert understated == {2, 3}  # level 1 (25 mW labelled "25") is honest


def test_tramp_mw_table_not_flagged():
    vtx = normalise_vtx(parse_diff(_TRAMP))
    assert vtx.power_unit == "mw"
    assert vtx.power_table[2] == 100
    assert vtx.power_global_mw == 100
    assert vtx.osd_power_mismatch is False


def test_betaflight_vtx_switch_reaches_200mw():
    vtx = normalise_vtx(parse_diff(BETAFLIGHT_DUMP))
    assert vtx.power_table_source == "vtxtable"
    assert vtx.power_table[1] == 25 and vtx.power_table[3] == 200
    # global index 1 -> 25 mW
    assert vtx.power_global_mw == 25
    # switches can select indices 1,2,3 -> max 200 mW while armed
    assert vtx.power_armed_max_mw == 200
    # low_power_disarm ON forces lowest (index 1) -> 25 mW while disarmed
    assert vtx.power_disarmed_mw == 25
    assert len(vtx.switches) == 3
    assert sorted(s.aux_channel for s in vtx.switches) == [2, 2, 2]


def test_inav_vtx_compliant_mw_table():
    vtx = normalise_vtx(parse_diff(INAV_DUMP), variant="INAV")
    assert vtx.power_table_source == "vtxtable"
    assert vtx.power_unit == "mw" and vtx.power_verifiable is True
    assert vtx.power_armed_max_mw == 25
    assert vtx.power_disarmed_mw == 25
    assert vtx.switches == []


def test_smartaudio_index_table_is_not_verifiable():
    vtx = normalise_vtx(parse_diff(SMARTAUDIO_INDEX_DUMP), device_type="SmartAudio")
    # opaque 0/1/2/3 indices -> real mW cannot be derived from the FC
    assert vtx.power_unit == "index"
    assert vtx.power_verifiable is False
    assert vtx.power_armed_max_mw is None
    assert all(lvl.real_mw is None for lvl in vtx.levels)
    # cannot compute real power, so no false manipulation claim
    assert vtx.osd_power_mismatch is False


def test_empty_vtx_control_slots_are_not_counted():
    # `dump all` lists all 10 control slots; only the configured one is a switch.
    diff = (
        "vtxtable powervalues 25 100 200 400 600\n"
        "vtxtable powerlabels 25 100 200 400 600\n"
        "vtx 0 0 0 0 0 0 0\n"
        "vtx 1 0 0 0 0 0 0\n"
        "vtx 2 2 0 0 3 1800 2100\n"   # the only real power switch
        "vtx 3 0 0 0 0 0 0\n"
        "vtx 4 1 0 0 0 1400 1700\n"   # has a range but power 0 -> band/channel only
        "set vtx_power = 1\n"
    )
    vtx = normalise_vtx(parse_diff(diff))
    assert len(vtx.switches) == 1
    assert vtx.switches[0].aux_channel == 2 and vtx.switches[0].power_index == 3


def test_device_type_disambiguates_tramp_25mw():
    # A bare "25" is 25 mW on a Tramp device, NOT 25 dBm.
    diff = "vtxtable powervalues 25 100\nvtxtable powerlabels 25 100\nset vtx_power = 1\n"
    vtx = normalise_vtx(parse_diff(diff), device_type="Tramp")
    assert vtx.power_unit == "mw"
    assert vtx.power_table[1] == 25 and vtx.power_verifiable is True


def test_inav_vtx_power_on_switch_is_detected():
    vtx = normalise_vtx(parse_diff(INAV_DUMP_SWITCH), variant="INAV")
    # logic op 25 driven by RC channel 6 -> a pilot switch controls VTX power
    assert len(vtx.switches) == 1
    assert vtx.switches[0].aux_channel == 6
    # dynamic RC-driven power can reach the whole table -> worst case > 25 mW
    assert vtx.power_armed_max_mw == max(vtx.power_table.values())
    assert vtx.power_armed_max_mw > 25


def test_inav_logic_chain_traces_to_rc_channel():
    # op25 reads logic condition 1, which scales RC channel 9 -> trace back to 9
    diff = (
        "# INAV/MATEKF405 7.1.0 Apr 21 2024 / 13:25:29 (03a5c1922)\n"
        "logic 0 1 -1 15 1 9 0 1000 0\n"   # LC0: scale RC ch9
        "logic 1 1 -1 37 4 0 0 3 0\n"      # LC1: derived from LC0
        "logic 2 1 -1 25 4 1 0 0 0\n"      # LC2: set VTX power from LC1
    )
    vtx = normalise_vtx(parse_diff(diff), variant="INAV")
    assert len(vtx.switches) == 1
    assert vtx.switches[0].aux_channel == 9
    assert vtx.power_armed_max_mw > 25
