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


# -- distribution (list / package / install) --------------------------------


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _dist_session(monkeypatch):
    s = BfcdSession(Settings(), CONFIG_DIR)
    monkeypatch.setattr(s, "_check_wsl", lambda: None)
    return s


def test_list_cache_parses_families_and_static_flag(monkeypatch):
    s = _dist_session(monkeypatch)
    monkeypatch.setattr(s, "_wsl_b64", lambda *a, **k: _Completed(
        stdout="2025.12\t1475472\tstatic\n4.4\t388896\tdynamic\n"))
    assert s.list_cache() == [
        {"family": "2025.12", "bytes": 1475472, "static": True},
        {"family": "4.4", "bytes": 388896, "static": False},
    ]


def test_install_bundle_rejects_non_bundle(monkeypatch):
    s = _dist_session(monkeypatch)
    monkeypatch.setattr(s, "_winpath_to_wsl", lambda p: "/mnt/c/x.tar.gz")
    monkeypatch.setattr(s, "_wsl_b64",
                        lambda *a, **k: _Completed(stdout="./some-other-file\n"))
    with pytest.raises(BfcdError, match="not a bf-configd bundle"):
        s.install_bundle(r"C:\x.tar.gz")


def test_install_bundle_extracts_and_returns_families(monkeypatch):
    s = _dist_session(monkeypatch)
    monkeypatch.setattr(s, "_winpath_to_wsl", lambda p: "/mnt/c/bundle.tar.gz")

    def fake(script, **k):
        if "tar -tzf" in script:
            return _Completed(stdout="./2025.12/bf-configd.elf\n"
                                     "./4.4/bf-configd.elf\n./SHA256SUMS\n")
        return _Completed(returncode=0)

    monkeypatch.setattr(s, "_wsl_b64", fake)
    assert s.install_bundle(r"C:\bundle.tar.gz") == ["2025.12", "4.4"]


def test_install_bundle_fails_on_checksum_mismatch(monkeypatch):
    s = _dist_session(monkeypatch)
    monkeypatch.setattr(s, "_winpath_to_wsl", lambda p: "/mnt/c/bundle.tar.gz")

    def fake(script, **k):
        if "tar -tzf" in script:
            return _Completed(stdout="./4.4/bf-configd.elf\n")
        return _Completed(returncode=1, stderr="bf-configd.elf: FAILED")

    monkeypatch.setattr(s, "_wsl_b64", fake)
    with pytest.raises(BfcdError, match="install failed"):
        s.install_bundle(r"C:\bundle.tar.gz")
