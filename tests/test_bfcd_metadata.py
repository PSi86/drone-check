from drone_check.bfcd.metadata import detect_metadata, firmware_family
from drone_check.demo import BETAFLIGHT_DUMP, INAV_DUMP


def test_firmware_family_semantic_and_date():
    assert firmware_family("4.5.3") == "4.5"
    assert firmware_family("4.4.0") == "4.4"
    assert firmware_family("2025.12.1") == "2025.12"
    assert firmware_family("4.5.0-RC1") == "4.5"
    assert firmware_family("") == ""
    assert firmware_family("garbage") == ""


def test_detect_betaflight_dump_is_clean():
    md = detect_metadata(BETAFLIGHT_DUMP)
    assert md.is_betaflight
    assert md.variant == "BTFL"
    assert md.version == "4.5.1"
    assert md.firmware_family == "4.5"
    assert md.target == "STM32F405"
    assert md.board_name == "HBFCS405"
    assert md.msp_api == "1.46"
    assert md.has_identity
    # A complete Betaflight dump has target + msp_api, so no warnings.
    assert md.warnings == []


def test_detect_inav_is_flagged_unsupported():
    md = detect_metadata(INAV_DUMP)
    assert not md.is_betaflight
    assert not md.has_identity  # family alone is not enough — must be Betaflight
    assert any("not Betaflight" in w for w in md.warnings)


def test_detect_empty_dump_warns_no_version():
    md = detect_metadata("")
    assert md.version == ""
    assert md.firmware_family == ""
    assert not md.has_identity
    assert any("no firmware version header" in w for w in md.warnings)
