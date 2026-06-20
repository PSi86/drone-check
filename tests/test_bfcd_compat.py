from pathlib import Path

from drone_check.bfcd.compat import BfcdStatus, load_matrix, select_backend
from drone_check.bfcd.metadata import DumpMetadata

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

MATRIX = {
    "4.5": {"status": "mvp", "backend": "bf-configd-4.5", "app": "10.10.x"},
    "4.4": {"status": "phase2", "backend": "bf-configd-4.4", "app": "10.10.x"},
}


def _md(**kw) -> DumpMetadata:
    base = dict(variant="BTFL", version="4.5.3", firmware_family="4.5",
                target="STM32F405")
    base.update(kw)
    return DumpMetadata(**base)


def test_mvp_family_selected_native_context():
    sel = select_backend(_md(), MATRIX)
    assert sel.status is BfcdStatus.MVP
    assert sel.backend == "bf-configd-4.5"
    assert sel.target_context == "native"
    assert sel.serveable


def test_planned_family_is_serveable_but_planned():
    sel = select_backend(_md(version="4.4.0", firmware_family="4.4"), MATRIX)
    assert sel.status is BfcdStatus.PLANNED
    assert sel.backend == "bf-configd-4.4"
    assert sel.serveable


def test_unknown_family_unsupported():
    sel = select_backend(_md(version="9.9.0", firmware_family="9.9"), MATRIX)
    assert sel.status is BfcdStatus.UNSUPPORTED
    assert not sel.serveable
    assert any("not in the compatibility matrix" in w for w in sel.warnings)


def test_non_betaflight_unsupported():
    sel = select_backend(_md(variant="INAV"), MATRIX)
    assert sel.status is BfcdStatus.UNSUPPORTED
    assert not sel.serveable


def test_missing_target_falls_back_to_generic():
    sel = select_backend(_md(target=""), MATRIX)
    assert sel.status is BfcdStatus.MVP
    assert sel.target_context == "generic"
    assert any("generic target context" in w for w in sel.warnings)


def test_real_matrix_loads_with_mvp_family():
    families = load_matrix(CONFIG_DIR)
    assert "4.5" in families
    assert families["4.5"]["status"] == "mvp"
    sel = select_backend(_md(), families)
    assert sel.status is BfcdStatus.MVP
