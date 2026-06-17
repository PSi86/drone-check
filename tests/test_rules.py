import copy

import pytest

from drone_check.capture import build_snapshot
from drone_check.config import load_config
from drone_check.demo import betaflight_profile, inav_profile, seed_allowlist
from drone_check.firmware import FirmwareVerifier, verify_snapshot

pytest.importorskip("celpy")
from drone_check.rules import RuleEngine, load_rules  # noqa: E402

CONFIG_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "config"


def _engine():
    cfg = load_config(CONFIG_DIR)
    return RuleEngine(load_rules(cfg.rules)), cfg


def _verifier(cfg, seed: bool):
    allow = copy.deepcopy(cfg.allowlist)
    if seed:
        seed_allowlist(allow)
    # offline-only check for deterministic tests
    return FirmwareVerifier(allow, use_allowlist=True, use_github=False)


def test_betaflight_fails_armed_power_rule():
    engine, cfg = _engine()
    profile = betaflight_profile()
    snap = build_snapshot(profile.identity, profile.cli_outputs, captured_at="t0")
    verify_snapshot(snap, _verifier(cfg, seed=True))

    ev = engine.evaluate(snap)
    assert ev.passed is False
    failed = {r.rule_id for r in ev.failed_rules}
    assert "vtx-power-armed-max" in failed
    assert "vtx-no-switch-exceeds-25" in failed
    # disarmed is forced to 25 mW, so that rule passes
    assert "vtx-power-disarmed-max" not in failed


def test_inav_passes_when_hash_approved():
    engine, cfg = _engine()
    profile = inav_profile()
    snap = build_snapshot(profile.identity, profile.cli_outputs, captured_at="t0")
    verify_snapshot(snap, _verifier(cfg, seed=True))

    ev = engine.evaluate(snap)
    critical_failed = [r for r in ev.failed_rules if r.severity == "critical"]
    assert critical_failed == []
    assert ev.passed is True


def test_inav_fails_when_hash_not_approved():
    engine, cfg = _engine()
    profile = inav_profile()
    snap = build_snapshot(profile.identity, profile.cli_outputs, captured_at="t0")
    # An unapproved build (e.g. a self-compiled hash) must fail the hash rule.
    snap.firmware.git_hash = "deadbeef"
    verify_snapshot(snap, _verifier(cfg, seed=False))

    ev = engine.evaluate(snap)
    assert ev.passed is False
    assert "firmware-hash-approved" in {r.rule_id for r in ev.failed_rules}
