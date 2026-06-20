from pathlib import Path

from drone_check.bfcd.goldens import CompareMask, compare_payload, load_masks

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_exact_equal_and_diff():
    assert compare_payload(b"\x01\x02\x03", b"\x01\x02\x03").equal
    res = compare_payload(b"\x01\x02\x03", b"\x01\xff\x03")
    assert not res.equal
    assert "offsets: 1" in res.detail


def test_length_mismatch_is_difference_even_when_masked():
    mask = CompareMask(mode="masked", ignore_bytes=[[0, 100]])
    res = compare_payload(b"\x01\x02", b"\x01\x02\x03", mask)
    assert not res.equal
    assert "length differs" in res.detail


def test_masked_ignores_dynamic_bytes():
    mask = CompareMask(mode="masked", ignore_bytes=[[0, 2]])
    # First two bytes differ but are masked; rest matches -> equal.
    assert compare_payload(b"\xaa\xbb\x10", b"\x00\x00\x10", mask).equal
    # A byte outside the mask differs -> not equal.
    assert not compare_payload(b"\xaa\xbb\x10", b"\x00\x00\x11", mask).equal


def test_load_masks_real_config():
    masks = load_masks(CONFIG_DIR)
    assert masks["MSP_API_VERSION"].mode == "exact"
    status = masks["MSP_STATUS"]
    assert status.mode == "masked"
    assert [0, 4] in status.ignore_bytes
