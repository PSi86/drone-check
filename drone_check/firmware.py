"""Firmware-hash verification.

The short git revision reported by the firmware (``version`` line / MSP build
info) identifies the source commit it was built from. We approve it in two ways,
controlled by config:

1. **Allowlist** – a local, version-pinned list of approved hashes (offline).
2. **GitHub**   – resolve the *claimed* version's release tag in the official
   firmware repository (``betaflight/betaflight`` / ``iNavFlight/inav``) and
   confirm the reported commit **is that tag's commit**, online.

Both checks are *version-bound*: a hash is approved only when it matches the
release of the version the firmware claims. The GitHub check deliberately does
**not** approve a commit merely because it exists somewhere in the repo — that
would let a drone report e.g. "2025.12.2" while running an unrelated commit. It
must be the exact commit the ``2025.12.2`` tag points to.

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
            ok, detail = self._check_github(variant, version, git_hash)
            if ok:
                return HashResult(True, "github", detail)
            # Fall through with the GitHub detail so the operator sees why.
            return HashResult(False, "none", detail)

        return HashResult(False, "none", f"{git_hash} not in allowlist")

    def _in_allowlist(self, variant: str, version: str, git_hash: str) -> bool:
        by_version = self._allowlist.get(variant, {})
        approved = by_version.get(version, [])
        return any(git_hash.startswith(h.lower()) or h.lower().startswith(git_hash) for h in approved)

    def _check_github(self, variant: str, version: str, git_hash: str) -> tuple[bool, str]:
        """Approve only if ``git_hash`` is the commit the ``version`` release tag
        points to in the official repo (version binding).

        We resolve the tag (not the bare commit), so a real-but-unrelated commit
        — a different version, an arbitrary repo commit — is rejected for the
        claimed version."""
        repo = _REPO_BY_VARIANT.get(variant)
        if not repo:
            return False, f"no known GitHub repo for variant {variant!r}"
        if not version:
            return False, "firmware reported no version; cannot bind hash to a release tag"
        try:
            import httpx

            tag_sha, detail = self._resolve_tag_sha(repo, version)
            if tag_sha is None:
                return False, detail
            tag_sha = tag_sha.lower()
            if tag_sha.startswith(git_hash) or git_hash.startswith(tag_sha):
                return True, f"matches {repo}@{version} ({tag_sha[:12]})"
            return False, (
                f"{git_hash} is not the {version} release commit "
                f"({tag_sha[:12]}) in {repo}"
            )
        except Exception as exc:  # network down, offline bench, etc.
            return False, f"GitHub check failed: {exc}"

    def _resolve_tag_sha(self, repo: str, version: str) -> tuple[Optional[str], str]:
        """Resolve a release tag to its commit SHA. Tries the bare version (how
        Betaflight/INAV tag releases) and a ``v``-prefixed fallback."""
        import httpx

        headers = {"Accept": "application/vnd.github+json"}
        last = ""
        for tag in (version, f"v{version}"):
            # The commits endpoint resolves a ref (tag/branch/sha) to its commit,
            # dereferencing annotated tags to the underlying commit.
            url = f"https://api.github.com/repos/{repo}/commits/{tag}"
            resp = httpx.get(url, timeout=self._timeout, headers=headers)
            if resp.status_code == 200:
                return (resp.json().get("sha") or "").lower(), ""
            if resp.status_code == 404:
                last = f"no release tag {version!r} in {repo}"
                continue
            return None, f"GitHub returned HTTP {resp.status_code}"
        return None, last


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
