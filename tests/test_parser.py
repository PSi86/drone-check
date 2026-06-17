from drone_check.demo import BETAFLIGHT_DUMP, INAV_DUMP
from drone_check.parser import parse_diff, parse_version_line


def test_betaflight_version_line():
    info = parse_version_line(BETAFLIGHT_DUMP)
    assert info.variant == "BTFL"
    assert info.firmware_name == "Betaflight"
    assert info.target == "STM32F405"
    assert info.version == "4.5.1"
    assert info.git_hash == "77d01ba3b"
    assert info.msp_api == "1.46"


def test_inav_version_line():
    info = parse_version_line(INAV_DUMP)
    assert info.variant == "INAV"
    assert info.target == "MATEKF405"
    assert info.version == "7.1.0"
    assert info.git_hash == "aa8543654"


def test_parse_diff_settings_and_vtx():
    cfg = parse_diff(BETAFLIGHT_DUMP)
    assert cfg.settings["vtx_power"] == "1"
    assert cfg.settings["vtx_low_power_disarm"] == "ON"
    assert cfg.board_name == "HBFCS405"
    assert cfg.vtxtable["powervalues"] == [25, 100, 200, 400, 600]
    # three vtx control lines, each with 7 numeric args
    assert len(cfg.vtx_lines) == 3
    assert cfg.vtx_lines[2] == [2, 2, 0, 0, 3, 1800, 2100]
