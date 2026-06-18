"""Firmware-hash verification.

The short git revision reported by the firmware (``version`` line / MSP build
info) identifies the source commit it was built from. We establish two *facts*
about it (always the same way, so they document the firmware regardless of
policy):

1. **Allowlist** – is the hash in the local, version-pinned list of approved
   release hashes? (offline; the strongest match — an exact official release.)
2. **GitHub**    – does the commit exist in the official firmware repository
   (``betaflight/betaflight`` / ``iNavFlight/inav``)? (online; it is a real
   commit of that firmware, though not necessarily a tagged release.)

The reported ``source`` ("allowlist" / "github" / "none") reflects those facts
and never depends on configuration. What *is* configurable is the **acceptance
level** — how those facts map to the approved / not-approved verdict:

* ``whitelist`` – approve only an exact allowlist (official-release) hash.
* ``official``  – approve an allowlist hash *or* any commit that exists in the
  official repository (the default; "all official builds").
* ``open``      – never reject for an unknown hash (approve regardless).

So the same hash can be approved or not depending on the level, while what is
documented about it stays identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_REPO_BY_VARIANT = {
    "BTFL": "betaflight/betaflight",
    "INAV": "iNavFlight/inav",
    "EMUF": "emuflight/EmuFlight",
}

# Acceptance levels, from strict to permissive. See module docstring.
ACCEPTANCE_LEVELS = ("whitelist", "official", "open")
DEFAULT_ACCEPTANCE = "official"


@dataclass
class HashResult:
    approved: bool
    source: str  # "allowlist" | "github" | "none"
    detail: str = ""


class FirmwareVerifier:
    def __init__(
        self,
        allowlist: dict,
        acceptance_level: str = DEFAULT_ACCEPTANCE,
        use_github: bool = True,
        timeout: float = 5.0,
    ):
        # allowlist shape: { "BTFL": { "4.5.1": ["024f8e13d", ...] }, ... }
        self._allowlist = allowlist or {}
        self._level = acceptance_level if acceptance_level in ACCEPTANCE_LEVELS else DEFAULT_ACCEPTANCE
        # Network check toggle: off = fully offline (no GitHub lookups at all).
        self._use_github = use_github
        self._timeout = timeout

    def verify(self, variant: str, version: str, git_hash: str) -> HashResult:
        git_hash = (git_hash or "").lower()
        in_allowlist = bool(git_hash) and self._in_allowlist(variant, version, git_hash)

        # The GitHub fact (commit exists in the official repo) is established the
        # same way regardless of acceptance level, so the documented ``source``
        # is config-independent. Skip the network only when it cannot change the
        # documented source: already allow-listed, no hash, or GitHub disabled.
        repo_has: Optional[bool] = None
        github_detail = ""
        if git_hash and not in_allowlist and self._use_github:
            repo_has, github_detail = self._check_github(variant, git_hash)

        source, detail = self._document(
            variant, version, git_hash, in_allowlist, repo_has, github_detail
        )
        return HashResult(self._approve(in_allowlist, repo_has), source, detail)

    def _approve(self, in_allowlist: bool, repo_has: Optional[bool]) -> bool:
        """Map the verification facts to a verdict per the acceptance level."""
        if self._level == "open":
            return True
        if self._level == "whitelist":
            return in_allowlist
        return in_allowlist or bool(repo_has)  # "official"

    @staticmethod
    def _document(variant, version, git_hash, in_allowlist, repo_has, github_detail):
        """The config-independent documented source + human-readable detail."""
        if in_allowlist:
            return "allowlist", f"{git_hash} listed for {variant} {version}"
        if repo_has:
            return "github", github_detail
        if not git_hash:
            return "none", "no git hash reported by firmware"
        if repo_has is False:
            return "none", github_detail
        return "none", f"{git_hash} not in allowlist"

    def _in_allowlist(self, variant: str, version: str, git_hash: str) -> bool:
        by_version = self._allowlist.get(variant, {})
        approved = by_version.get(version, [])
        return any(git_hash.startswith(h.lower()) or h.lower().startswith(git_hash) for h in approved)

    def _check_github(self, variant: str, git_hash: str) -> tuple[bool, str]:
        """Whether ``git_hash`` is a real commit in the official repo. This is a
        *fact* (existence), not a verdict — the acceptance level decides whether
        existence is enough to approve."""
        repo = _REPO_BY_VARIANT.get(variant)
        if not repo:
            return False, f"no known GitHub repo for variant {variant!r}"
        try:
            import httpx

            url = f"https://api.github.com/repos/{repo}/commits/{git_hash}"
            resp = httpx.get(
                url,
                timeout=self._timeout,
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                sha = resp.json().get("sha", "")
                return True, f"commit {sha[:12]} found in {repo}"
            if resp.status_code == 404:
                return False, f"commit {git_hash} not found in {repo}"
            return False, f"GitHub returned HTTP {resp.status_code}"
        except Exception as exc:  # network down, offline bench, etc.
            return False, f"GitHub check failed: {exc}"


def verify_snapshot(snapshot, verifier: Optional[FirmwareVerifier]) -> None:
    """Run the hash check and write the result onto the snapshot in place."""
    if verifier is None:
        snapshot.firmware_hash_approved = False
        snapshot.firmware_hash_source = "none"
        return
    result = verifier.verify(
        snapshot.firmware.variant,
        snapshot.firmware.version,
        snapshot.firmware.git_hash,
    )
    snapshot.firmware_hash_approved = result.approved
    snapshot.firmware_hash_source = result.source
