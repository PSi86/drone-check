from pathlib import Path

import pytest

from drone_check.bfcd.session import BfcdError, BfcdNotBuilt, BfcdSession
from drone_check.config import Settings
from drone_check.demo import BETAFLIGHT_DUMP, INAV_DUMP

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def _session(monkeypatch, binary_present: bool) -> BfcdSession:
    s = BfcdSession(Settings(), CONFIG_DIR)
    # Keep the test hermetic: don't probe WSL / the filesystem for the binary.
    monkeypatch.setattr(s, "_binary_exists", lambda path: binary_present)
    return s


def test_prepare_betaflight_selects_backend(monkeypatch):
    s = _session(monkeypatch, binary_present=True)
    plan = s.prepare(BETAFLIGHT_DUMP)
    assert plan.metadata.firmware_family == "4.5"
    assert plan.selection.backend == "bf-configd-4.5"
    assert plan.binary_available
    assert plan.binary_path.endswith("/4.5/bf-configd.elf")


def test_prepare_inav_is_unserveable(monkeypatch):
    s = _session(monkeypatch, binary_present=False)
    with pytest.raises(BfcdError):
        s.prepare(INAV_DUMP)


def test_start_without_binary_raises_not_built(monkeypatch):
    s = _session(monkeypatch, binary_present=False)
    with pytest.raises(BfcdNotBuilt):
        s.start(BETAFLIGHT_DUMP)
