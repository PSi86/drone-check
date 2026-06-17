"""Firmware-hash verification.

The short git revision reported by the firmware (``version`` line / MSP build
info) identifies the source commit it was built from. We approve it in two ways,
controlled by config:

1. **Allowlist** – a local, version-pinned list of approved hashes (offline).
2. **GitHub**   – confirm the commit exists in the official firmware repository
   (``betaflight/betaflight`` / ``iNavFlight/inav``), online.

A short hash is accepted if *either* source approves it (when both are enabled).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_REPO_BY_VARIANT = {
    "BTFL": "betaflight/betaflight",
    "INAV": "iNavFlight/inav",
    "EMUF": "emuflight/EmuFlight",
}


@dataclass
class HashResult:
    approved: bool
    source: str  # "allowlist" | "github" | "none"
    detail: str = ""


class FirmwareVerifier:
    def __init__(
        self,
        allowlist: dict,
        use_allowlist: bool = True,
        use_github: bool = True,
        timeout: float = 5.0,
    ):
        # allowlist shape: { "BTFL": { "4.5.1": ["024f8e13d", ...] }, ... }
        self._allowlist = allowlist or {}
        self._use_allowlist = use_allowlist
        self._use_github = use_github
        self._timeout = timeout

    def verify(self, variant: str, version: str, git_hash: str) -> HashResult:
        git_hash = (git_hash or "").lower()
        if not git_hash:
            return HashResult(False, "none", "no git hash reported by firmware")

        if self._use_allowlist and self._in_allowlist(variant, version, git_hash):
            return HashResult(True, "allowlist", f"{git_hash} listed for {variant} {version}")

        if self._use_github:
            ok, detail = self._check_github(variant, git_hash)
            if ok:
                return HashResult(True, "github", detail)
            # Fall through with the GitHub detail so the operator sees why.
            return HashResult(False, "none", detail)

        return HashResult(False, "none", f"{git_hash} not in allowlist")

    def _in_allowlist(self, variant: str, version: str, git_hash: str) -> bool:
        by_version = self._allowlist.get(variant, {})
        approved = by_version.get(version, [])
        return any(git_hash.startswith(h.lower()) or h.lower().startswith(git_hash) for h in approved)

    def _check_github(self, variant: str, git_hash: str) -> tuple[bool, str]:
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
