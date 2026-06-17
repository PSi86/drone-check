from drone_check.demo import BETAFLIGHT_DUMP, INAV_DUMP, INAV_DUMP_SWITCH
from drone_check.parser import parse_diff
from drone_check.vtx import normalise_vtx


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


def test_inav_vtx_compliant_fallback_table():
    vtx = normalise_vtx(parse_diff(INAV_DUMP), variant="INAV")
    # no vtxtable in the INAV dump -> fallback table is used
    assert vtx.power_table_source == "default-fallback"
    assert vtx.power_armed_max_mw == 25
    assert vtx.power_disarmed_mw == 25
    assert vtx.switches == []


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
