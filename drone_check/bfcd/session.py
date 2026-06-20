"""Run a dump in the native bf-configd backend and expose it to the Configurator.

This is the integration seam inside drone-check (work package BFCD-012): given a
capture's ``dump all``, it detects the metadata, selects the backend from the
matrix, resolves the backend binary, and serves it over an MSP WebSocket the
Betaflight Configurator can connect to — mirroring :class:`drone_check.sitl.SitlRunner`
so the web UI can offer SITL and bf-configd side by side.

The backend binary is the real Betaflight CLI/config/MSP code, built from
official source with a read-only guard (it refuses every MSP write), produced by
``scripts/build_bfcd.sh``. Serving uses the same proven two-phase flow as SITL
(load the dump over the CLI, ``save`` which reboots, then serve from the
populated config) and reuses SITL's transport helpers rather than duplicating
them. The bf-configd difference is the firmware-enforced read-only guard: the
Configurator can view everything but cannot change or persist anything.

:meth:`prepare` is pure decision-making (metadata + backend selection); :meth:`start`
runs the backend and drives a :class:`BfcdStatus` the web UI polls while it loads.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..cli_session import supports_framed_cli
from ..config import Settings
from ..sitl import (
    _NEW_PROCESS_GROUP,
    _find_msp_port,
    _port_free,
    _wait_port,
    load_dump_over_cli,
)
from .compat import BackendSelection, load_matrix, select_backend
from .metadata import DumpMetadata, detect_metadata

# The backend binary's process name (comm, truncated to 15 chars) used for the
# pkill fallback that frees the TCP port after a reboot.
_PROC_NAME = "bf-configd.elf"


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


@dataclass
class BfcdStatus:
    """Live session state the web UI polls (parallels SitlStatus)."""

    running: bool
    starting: bool = False
    phase: str = "idle"  # checking|starting|loading|saving|starting2|proxy|ready|idle
    detail: str = ""
    sent: int = 0
    total: int = 0
    version: str | None = None
    capture_id: str | None = None
    connect_url: str | None = None


class BfcdSession:
    """Prepare and run a bf-configd backend for a single dump (one at a time)."""

    def __init__(self, settings: Settings, config_dir: Path):
        self.s = settings
        self._config_dir = Path(config_dir)
        self._matrix = load_matrix(self._config_dir)
        # On Windows the native backend (a Linux ELF, like SITL) runs under WSL;
        # on Linux it runs directly. macOS cannot run the Linux ELF.
        self._use_wsl = sys.platform == "win32"
        self._backend: subprocess.Popen | None = None
        self._proxy: subprocess.Popen | None = None
        # Memoized environment probe (gates whether the feature is offered).
        self._env_ok: bool | None = None
        # Progress state (updated from the start() worker thread, read by status()).
        self._lock = threading.Lock()
        self._phase = "idle"
        self._detail = ""
        self._sent = 0
        self._total = 0
        self._starting = False
        self._version: str | None = None
        self._capture_id: str | None = None
        self._connect_url: str | None = None

    # -- WSL / native dispatch (mirrors SitlRunner) -----------------------

    def _wsl(self, script: str, *, capture: bool = False, timeout: float | None = None):
        """Run a bash script in the backend's Linux environment (WSL or native)."""
        if self._use_wsl:
            cmd = ["wsl", "-d", self.s.sitl_distro, "--", "bash", "-lc", script]
        else:
            cmd = ["bash", "-lc", script]
        if capture:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=_NEW_PROCESS_GROUP)

    # -- availability (UI gating) ----------------------------------------

    def wsl_available(self) -> bool:
        """Whether the backend's Linux environment is present (WSL on Windows,
        native on Linux, never on macOS). Mirrors SitlRunner.wsl_available."""
        if not self._use_wsl:
            return sys.platform.startswith("linux")
        try:
            res = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        if res.returncode != 0:
            return False
        out = res.stdout or b""
        text = out.decode("utf-16-le", "ignore")
        if self.s.sitl_distro not in text:
            text = out.decode("utf-8", "ignore")
        names = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return self.s.sitl_distro in names

    def available(self) -> bool:
        """Whether the bf-configd feature should be offered: enabled in config AND
        the Linux environment present. The environment probe is memoized."""
        if not self.s.bfcd_enabled:
            return False
        if self._env_ok is None:
            self._env_ok = self.wsl_available()
        return self._env_ok

    # -- planning (works without a backend binary) -----------------------

    def backend_binary_path(self, family: str) -> str:
        """Where the backend binary for a family is expected in the cache.

        Mirrors the SITL cache layout (one subdirectory per family). A WSL path
        on Windows, a native path on Linux.
        """
        return f"{self.s.bfcd_cache_dir}/{family}/bf-configd.elf"

    def _binary_exists(self, path: str) -> bool:
        """Best-effort check for the backend binary (never raises)."""
        if self._use_wsl:
            try:
                res = self._wsl(f"test -f {path} && echo yes", capture=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                return False
            return res.returncode == 0 and "yes" in (res.stdout or "")
        return Path(path.replace("~", str(Path.home()), 1)).is_file()

    def prepare(self, dump_text: str) -> BfcdPlan:
        """Detect metadata, select the backend and resolve its binary path.

        Raises :class:`BfcdError` only when the dump cannot be served at all
        (unsupported variant/family), so the caller can distinguish "won't work"
        from "not built yet".
        """
        md = detect_metadata(dump_text)
        sel = select_backend(md, self._matrix)
        if not sel.serveable:
            reason = "; ".join(sel.warnings) or "unsupported dump"
            raise BfcdError(f"bf-configd cannot serve this dump: {reason}")
        path = self.backend_binary_path(sel.family)
        return BfcdPlan(metadata=md, selection=sel, binary_path=path,
                        binary_available=self._binary_exists(path))

    # -- status -----------------------------------------------------------

    def _progress(self, phase: str, detail: str = "", sent: int = 0,
                  total: int = 0, starting: bool = True) -> None:
        with self._lock:
            self._phase = phase
            self._detail = detail
            self._sent = sent
            self._total = total
            self._starting = starting

    def status(self) -> BfcdStatus:
        running = self._backend is not None and self._backend.poll() is None
        with self._lock:
            active = running or self._starting
            return BfcdStatus(
                running=running,
                starting=self._starting,
                phase=self._phase,
                detail=self._detail,
                sent=self._sent,
                total=self._total,
                version=self._version if active else None,
                capture_id=self._capture_id if active else None,
                connect_url=f"ws://127.0.0.1:{self.s.bfcd_ws_port}" if running else None,
            )

    # -- running ----------------------------------------------------------

    def _teardown(self) -> None:
        """Kill the proxy + backend and free the TCP port (never raises)."""
        for proc in (self._proxy, self._backend):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._proxy = self._backend = None
        try:
            self._wsl(f"pkill -x {_PROC_NAME}", capture=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    def stop(self) -> None:
        self._teardown()
        with self._lock:
            self._version = self._capture_id = self._connect_url = None
        self._progress("idle", "", starting=False)

    # bf-configd holds no WSL ownership of its own (SitlRunner manages the WSL
    # lifecycle); shutdown just ends the session.
    shutdown = stop

    def _wait_port_free(self, host: str, port: int,
                        attempts: int = 40, delay: float = 0.15) -> bool:
        for _ in range(attempts):
            if _port_free(host, port):
                return True
            time.sleep(delay)
        return False

    def start(self, dump_text: str, capture_id: str = "adhoc",
              version: str = "") -> BfcdStatus:
        """Load ``dump_text`` into the backend and expose it for the Configurator.

        Two-phase, exactly like SITL: load the dump over the CLI and ``save``
        (which reboots the backend), then serve from the populated config and
        bridge MSP to a WebSocket. Updates :meth:`status` as it runs so the web UI
        can show progress, and returns the ready status. Raises
        :class:`BfcdNotBuilt` if the backend isn't built, :class:`BfcdError` on any
        start failure.
        """
        self._progress("checking", "Checking bf-configd binary…")
        plan = self.prepare(dump_text)
        with self._lock:
            self._version = version or plan.metadata.version
            self._capture_id = capture_id
        if not plan.binary_available:
            self._progress("idle", "", starting=False)
            raise BfcdNotBuilt(
                f"no bf-configd backend for firmware family {plan.selection.family}; "
                f"build it with `bash scripts/build_bfcd.sh {plan.metadata.version}` "
                f"(expected at {plan.binary_path})"
            )

        host = "127.0.0.1"
        tcp = self.s.bfcd_tcp_port
        elf = plan.binary_path
        run_dir = f"{self.s.bfcd_run_dir}/{capture_id}"
        framed = supports_framed_cli(plan.metadata.version)

        self._teardown()  # only one session at a time

        try:
            # Phase 1: boot fresh, push the dump through the CLI, save (reboots).
            self._progress("starting", "Starting bf-configd (load phase)…")
            loader = self._wsl(f"rm -rf {run_dir} && mkdir -p {run_dir} && cd {run_dir} "
                               f"&& exec {elf} >/dev/null 2>&1")
            try:
                if not _wait_port(host, tcp, timeout=self.s.bfcd_boot_timeout):
                    raise BfcdError("bf-configd did not start (load phase)")
                self._progress("loading", "Loading configuration…", 0, 0)
                load_dump_over_cli(
                    host, tcp, dump_text, framed=framed,
                    progress_cb=lambda sent, total: self._progress(
                        "loading", "Loading configuration…", sent, total))
            finally:
                self._progress("saving", "Saving and rebooting bf-configd…",
                               self._sent, self._total)
                if loader.poll() is None:
                    try:
                        loader.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        loader.terminate()
            # The save-triggered reboot should free the port; force it if not.
            if not self._wait_port_free(host, tcp):
                try:
                    self._wsl(f"pkill -x {_PROC_NAME}", capture=True, timeout=15)
                except (OSError, subprocess.SubprocessError):
                    pass
                self._wait_port_free(host, tcp)

            # Phase 2: serve from the saved config; this is the session backend.
            self._progress("starting2", "Starting bf-configd from saved configuration…")
            self._backend = self._wsl(f"cd {run_dir} && exec {elf} >/dev/null 2>&1")
            msp_port = _find_msp_port(host, tcp, count=8, timeout=self.s.bfcd_boot_timeout)
            if msp_port is None:
                raise BfcdError("bf-configd did not start (serve phase)")

            # websockify so the WebSocket-only web Configurator can connect.
            self._progress("proxy", "Starting Configurator proxy…")
            self._proxy = subprocess.Popen(
                [sys.executable, "-m", "websockify",
                 f"127.0.0.1:{self.s.bfcd_ws_port}", f"127.0.0.1:{msp_port}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NEW_PROCESS_GROUP,
            )
            if not _wait_port(host, self.s.bfcd_ws_port, timeout=10):
                raise BfcdError("websockify proxy did not start "
                                "(is the 'websockify' package installed?)")
        except BfcdError:
            self._teardown()
            self._progress("idle", "", starting=False)
            raise
        except Exception as exc:
            self._teardown()
            self._progress("idle", "", starting=False)
            raise BfcdError(f"bf-configd start failed: {exc}") from exc

        self._progress("ready", "Ready", starting=False)
        return self.status()
