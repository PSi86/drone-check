"""Wrapper that turns a dump into a (future) live bf-configd backend session.

This is the integration seam inside drone-check (work package BFCD-012): given a
capture's ``dump all``, it detects the metadata, selects the backend from the
matrix, resolves where that backend binary would live, and — once the native
CONFIGD backend exists — will launch it and expose an MSP WebSocket endpoint for
the Configurator, mirroring :class:`drone_check.sitl.SitlRunner`.

In this scaffolding stage the native backend is **not built yet**, so
:meth:`BfcdSession.start` raises :class:`BfcdNotBuilt` with a precise,
actionable message. :meth:`BfcdSession.prepare` is fully functional and is what
the UI/CLI use to show the operator what *would* happen (which backend, which
status, which warnings) before any backend exists.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from .compat import BackendSelection, load_matrix, select_backend
from .metadata import DumpMetadata, detect_metadata


class BfcdError(RuntimeError):
    """A bf-configd session could not be prepared or started (operator-facing)."""


class BfcdNotBuilt(BfcdError):
    """The selected backend binary does not exist (it must be built first)."""


@dataclass
class BfcdPlan:
    """Everything decided about a dump before a backend is launched."""

    metadata: DumpMetadata
    selection: BackendSelection
    binary_path: str
    binary_available: bool

    def to_dict(self) -> dict:
        return {
            "metadata": self.metadata.to_dict(),
            "selection": self.selection.to_dict(),
            "binary_path": self.binary_path,
            "binary_available": self.binary_available,
        }


class BfcdSession:
    """Prepare (and, later, run) a bf-configd backend for a single dump."""

    def __init__(self, settings: Settings, config_dir: Path):
        self.s = settings
        self._config_dir = Path(config_dir)
        self._matrix = load_matrix(self._config_dir)
        # On Windows the native backend (a Linux ELF, like SITL) runs under WSL;
        # on Linux it runs directly. macOS cannot run the Linux ELF.
        self._use_wsl = sys.platform == "win32"

    # -- planning (works today) ------------------------------------------

    def backend_binary_path(self, family: str) -> str:
        """Where the backend binary for a family is expected in the cache.

        Mirrors the SITL cache layout: one subdirectory per firmware family. The
        path is a WSL path on Windows and a native path on Linux.
        """
        return f"{self.s.bfcd_cache_dir}/{family}/bf-configd.elf"

    def _binary_exists(self, path: str) -> bool:
        """Best-effort check for the backend binary (never raises)."""
        if self._use_wsl:
            try:
                res = subprocess.run(
                    ["wsl", "-d", self.s.sitl_distro, "--", "bash", "-lc",
                     f"test -f {path} && echo yes"],
                    capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                return False
            return res.returncode == 0 and "yes" in (res.stdout or "")
        return Path(path.replace("~", str(Path.home()), 1)).is_file()

    def prepare(self, dump_text: str) -> BfcdPlan:
        """Detect metadata, select the backend and resolve its binary path.

        Pure decision-making plus a single existence probe — no backend is
        started. Raises :class:`BfcdError` only when the dump cannot be served at
        all (unsupported variant/family), so the caller can distinguish "won't
        work" from "not built yet".
        """
        md = detect_metadata(dump_text)
        sel = select_backend(md, self._matrix)
        if not sel.serveable:
            reason = "; ".join(sel.warnings) or "unsupported dump"
            raise BfcdError(f"bf-configd cannot serve this dump: {reason}")
        path = self.backend_binary_path(sel.family)
        return BfcdPlan(metadata=md, selection=sel, binary_path=path,
                        binary_available=self._binary_exists(path))

    # -- running (native backend not implemented yet) --------------------

    def start(self, dump_text: str):
        """Launch the backend for ``dump_text`` and expose it for the Configurator.

        Not yet implemented: the native CONFIGD backend does not exist in this
        scaffolding stage. This validates and selects the backend (so the error
        is specific) and then raises :class:`BfcdNotBuilt`.
        """
        plan = self.prepare(dump_text)
        if not plan.binary_available:
            raise BfcdNotBuilt(
                f"no bf-configd backend for firmware family {plan.selection.family}; "
                f"build it with `bash scripts/build_bfcd.sh {plan.selection.family}` "
                f"(expected at {plan.binary_path})"
            )
        # The binary exists but the launch/serve path is not implemented yet.
        raise BfcdNotBuilt(
            "the native bf-configd backend launch is not implemented yet; "
            "for now use the SITL-based 'view in Configurator' feature"
        )
