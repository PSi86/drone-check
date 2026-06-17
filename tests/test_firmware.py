from pathlib import Path

from drone_check.config import load_config
from drone_check.firmware import FirmwareVerifier

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _verifier():
    cfg = load_config(CONFIG_DIR)
    # offline only: prove the generated allowlist contains real release hashes
    return FirmwareVerifier(cfg.allowlist, use_allowlist=True, use_github=False)


def test_real_betaflight_release_hash_is_approved():
    v = _verifier()
    # firmware reports an abbreviation of the tag commit SHA
    assert v.verify("BTFL", "4.5.1", "77d01ba3b").approved
    assert v.verify("BTFL", "4.4.3", "738127e7e").approved


def test_real_inav_release_hash_is_approved():
    v = _verifier()
    assert v.verify("INAV", "7.1.0", "aa8543654").approved
    assert v.verify("INAV", "8.0.0", "ec2106af4").approved


def test_unknown_hash_is_rejected_offline():
    v = _verifier()
    assert not v.verify("BTFL", "4.5.1", "deadbeef").approved
    # right hash but wrong version key -> not approved
    assert not v.verify("BTFL", "9.9.9", "77d01ba3b").approved
