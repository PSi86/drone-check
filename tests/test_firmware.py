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


# --- version-bound GitHub check ------------------------------------------------

class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _fake_get(tag_to_sha):
    """Fake httpx.get resolving /commits/<ref> to a SHA via tag_to_sha; 404 else."""
    def get(url, timeout=None, headers=None):
        ref = url.rstrip("/").split("/")[-1]
        if ref in tag_to_sha:
            return _FakeResp(200, {"sha": tag_to_sha[ref]})
        return _FakeResp(404)
    return get


def _github_only():
    return FirmwareVerifier({}, use_allowlist=False, use_github=True)


def test_github_approves_exact_release_commit(monkeypatch):
    import httpx
    tag_sha = "79065c96ba0bb5cdc675e67d7093e05dab8b330e"  # 2025.12.2 tag
    monkeypatch.setattr(httpx, "get", _fake_get({"2025.12.2": tag_sha}))
    r = _github_only().verify("BTFL", "2025.12.2", "79065c96b")
    assert r.approved and r.source == "github"


def test_github_rejects_real_but_wrong_version_commit(monkeypatch):
    import httpx
    tag_sha = "4605309d8253db0113d4c54d31fe8bd998f46401"  # 4.4.0 release tag
    monkeypatch.setattr(httpx, "get", _fake_get({"4.4.0": tag_sha}))
    # 6f1cac69e is a genuine Betaflight commit but NOT the 4.4.0 release: a drone
    # labelling itself 4.4.0 while running it must NOT pass the version binding.
    r = _github_only().verify("BTFL", "4.4.0", "6f1cac69e")
    assert not r.approved
    assert "not the 4.4.0 release commit" in r.detail


def test_github_rejects_unknown_version_tag(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get({}))  # every ref 404s
    r = _github_only().verify("BTFL", "9.9.9", "deadbeef")
    assert not r.approved
    assert "no release tag" in r.detail


def test_github_accepts_v_prefixed_tag(monkeypatch):
    import httpx
    tag_sha = "abcdef1234567890abcdef1234567890abcdef12"
    monkeypatch.setattr(httpx, "get", _fake_get({"v1.2.3": tag_sha}))
    r = _github_only().verify("BTFL", "1.2.3", "abcdef123")
    assert r.approved
