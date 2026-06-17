from drone_check.capture import build_snapshot
from drone_check.demo import betaflight_profile, inav_profile


def test_build_snapshot_betaflight():
    profile = betaflight_profile()
    snap = build_snapshot(profile.identity, profile.cli_outputs, captured_at="t0")
    assert snap.uid == profile.identity.uid
    assert snap.firmware.variant == "BTFL"
    assert snap.firmware.firmware_name == "Betaflight"
    assert snap.firmware.git_hash == "77d01ba3b"
    assert snap.firmware.board_name == "HBFCS405"
    assert snap.vtx.power_armed_max_mw == 200
    assert snap.settings["vtx_low_power_disarm"] == "ON"
    # names read from the FC (Betaflight craft_name / pilot_name)
    assert snap.craft_name == "TESTQUAD"
    assert snap.pilot_name == "MAX POWER"


def test_build_snapshot_inav():
    profile = inav_profile()
    snap = build_snapshot(profile.identity, profile.cli_outputs, captured_at="t0")
    assert snap.firmware.variant == "INAV"
    assert snap.vtx.power_armed_max_mw == 25
    # INAV craft name comes from `name`, pilot from `pilot_name`
    assert snap.craft_name == "WINGONE"
    assert snap.pilot_name == "ANNA"
