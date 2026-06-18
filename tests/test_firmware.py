from pathlib import Path

from drone_check.config import load_config
from drone_check.firmware import FirmwareVerifier

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _verifier():
    cfg = load_config(CONFIG_DIR)
    # offline, whitelist-only: prove the generated allowlist contains real hashes
    return FirmwareVerifier(cfg.allowlist, acceptance_level="whitelist", use_github=False)


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


# --- acceptance levels + GitHub existence check --------------------------------

class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _fake_commits(known_hashes):
    """Fake httpx.get for /commits/<sha>: 200 for a known commit, 404 otherwise."""
    known = {h.lower() for h in known_hashes}

    def get(url, timeout=None, headers=None):
        ref = url.rstrip("/").split("/")[-1].lower()
        if ref in known:
            return _FakeResp(200, {"sha": ref + "0" * (40 - len(ref))})
        return _FakeResp(404)
    return get


def _verifier_at(level, allowlist=None):
    return FirmwareVerifier(allowlist or {}, acceptance_level=level, use_github=True)


def test_official_level_approves_existing_repo_commit(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", _fake_commits({"6f1cac69e"}))
    r = _verifier_at("official").verify("BTFL", "4.4.0", "6f1cac69e")
    assert r.approved and r.source == "github"


def test_official_level_rejects_nonexistent_commit(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", _fake_commits(set()))  # 404 for everything
    r = _verifier_at("official").verify("BTFL", "4.4.0", "deadbeef")
    assert not r.approved and r.source == "none"


def test_whitelist_level_rejects_repo_commit_not_in_allowlist(monkeypatch):
    import httpx
    # The commit exists in the repo (would pass "official"), but whitelist mode
    # only approves exact allowlist entries -> not approved, yet still documented
    # as a github commit (display is config-independent).
    monkeypatch.setattr(httpx, "get", _fake_commits({"6f1cac69e"}))
    r = _verifier_at("whitelist").verify("BTFL", "4.4.0", "6f1cac69e")
    assert not r.approved
    assert r.source == "github"


def test_open_level_approves_unknown_hash(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", _fake_commits(set()))  # unknown everywhere
    r = _verifier_at("open").verify("BTFL", "4.4.0", "deadbeef")
    assert r.approved          # open never rejects
    assert r.source == "none"  # but documents that it was not found


def test_source_is_independent_of_acceptance_level(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", _fake_commits({"6f1cac69e"}))
    results = {lvl: _verifier_at(lvl).verify("BTFL", "4.4.0", "6f1cac69e")
              for lvl in ("whitelist", "official", "open")}
    # Same documented source regardless of level...
    assert {r.source for r in results.values()} == {"github"}
    # ...but the verdict differs by level.
    assert results["whitelist"].approved is False
    assert results["official"].approved is True
    assert results["open"].approved is True


def test_use_github_false_skips_network(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise AssertionError("network must not be used when use_github is false")

    monkeypatch.setattr(httpx, "get", boom)
    v = FirmwareVerifier({}, acceptance_level="official", use_github=False)
    r = v.verify("BTFL", "4.4.0", "6f1cac69e")
    assert not r.approved and r.source == "none"
