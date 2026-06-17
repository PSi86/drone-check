"""Run a stored capture's configuration in a Betaflight SITL instance.

drone-check's "view in Configurator" feature loads a capture's ``dump all`` into
a *version-matched* Betaflight SITL (Software-In-The-Loop) instance, so an
inspector can connect the real Betaflight web Configurator to it and see exactly
what the drone's owner would see. All firmware-version-specific GUI behaviour is
then handled by the real Configurator, not by us.

How it works (Windows host + SITL built for Linux, run under WSL):

* SITL binaries are **pre-built** per firmware version by ``scripts/build_sitl.sh``
  into a cache directory inside WSL. drone-check never builds — it only selects.
* SITL exposes UART1 on TCP ``127.0.0.1:5761``; WSL2 forwards that to the Windows
  host, so the host-side loader and the browser both reach it over localhost.
* Loading is two-phase because ``save`` reboots SITL (the process exits):
    1. start SITL with a fresh ``eeprom.bin``, push the dump over the CLI, ``save``
       (SITL writes the eeprom and exits),
    2. relaunch SITL in the same directory — it now boots from the populated
       eeprom with the capture's configuration.
* The web Configurator (2025.12+) speaks WebSocket only, so we run a ``websockify``
  proxy ``ws://127.0.0.1:6761`` → ``tcp://127.0.0.1:5761``.

Known limitation: stock SITL builds omit VTX support, so VTX settings are not
visible in this view. drone-check's own dump analysis remains the authoritative
source for VTX power. (A VTX-enabled SITL build is a planned follow-up.)
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


class SitlError(RuntimeError):
    """A SITL session could not be started; the reason is operator-facing."""


# ---- low-level CLI over TCP -------------------------------------------------


def _drain(sock: socket.socket, total: float = 1.5, idle: float = 0.3) -> bytes:
    """Read whatever the firmware sends until it goes quiet or ``total`` elapses."""
    sock.setblocking(False)
    buf = bytearray()
    start = last = time.monotonic()
    while time.monotonic() - start < total:
        try:
            chunk = sock.recv(8192)
            if chunk:
                buf += chunk
                last = time.monotonic()
        except BlockingIOError:
            if buf and time.monotonic() - last > idle:
                break
            time.sleep(0.02)
    return bytes(buf)


def load_dump_over_cli(host: str, port: int, dump_text: str) -> None:
    """Enter the SITL CLI, replay a ``dump all`` batch and persist it with ``save``.

    Comment and ``batch``/``save`` framing lines are dropped — we drive ``save``
    ourselves. ``resource``/timer/dma/board lines are sent as-is; SITL rejects
    them (no GPIO on a host target), which is harmless for viewing the config.
    """
    sock = socket.create_connection((host, port), timeout=5)
    try:
        sock.sendall(b"#\r\n")  # enter CLI mode
        time.sleep(0.4)
        _drain(sock, 1.5)

        for raw in dump_text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line in ("batch start", "batch end", "save"):
                continue
            sock.sendall(line.encode("utf-8", "replace") + b"\r\n")
            # Pace the writes and keep draining the echo so SITL's TX buffer
            # never backs up and stalls its CLI task.
            _drain(sock, 0.02, idle=0.01)

        _drain(sock, 1.0)
        sock.sendall(b"save\r\n")
        _drain(sock, 3.0)
    finally:
        sock.close()


def _wait_port(host: str, port: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _port_free(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return False
    except OSError:
        return True


# ---- session ----------------------------------------------------------------


@dataclass
class SitlStatus:
    running: bool
    version: str | None = None
    capture_id: str | None = None
    connect_url: str | None = None


class SitlRunner:
    """Manages a single SITL session (one at a time)."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._sitl: subprocess.Popen | None = None
        self._proxy: subprocess.Popen | None = None
        self._version: str | None = None
        self._capture_id: str | None = None

    # -- helpers ----------------------------------------------------------

    def _wsl(self, script: str, *, capture: bool = False, timeout: float | None = None):
        cmd = ["wsl", "-d", self.s.sitl_distro, "--", "bash", "-lc", script]
        if capture:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _binary_path(self, version: str) -> str:
        # WSL path; ~ expands inside `bash -lc`.
        return f"{self.s.sitl_cache_dir}/{version}/betaflight_SITL.elf"

    def binary_available(self, version: str) -> bool:
        path = self._binary_path(version)
        try:
            res = self._wsl(f"test -f {path} && echo yes", capture=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0 and "yes" in (res.stdout or "")

    def _check_wsl(self) -> None:
        try:
            res = self._wsl("echo ok", capture=True, timeout=30)
        except FileNotFoundError as exc:
            raise SitlError("WSL not found — install WSL to use the Configurator view") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise SitlError(f"WSL not reachable: {exc}") from exc
        if res.returncode != 0 or "ok" not in (res.stdout or ""):
            raise SitlError(f"WSL distro '{self.s.sitl_distro}' not available")

    # -- lifecycle --------------------------------------------------------

    def status(self) -> SitlStatus:
        running = self._sitl is not None and self._sitl.poll() is None
        if not running:
            return SitlStatus(running=False)
        return SitlStatus(
            running=True,
            version=self._version,
            capture_id=self._capture_id,
            connect_url=f"ws://127.0.0.1:{self.s.sitl_ws_port}",
        )

    def stop(self) -> None:
        for proc in (self._proxy, self._sitl):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._proxy = self._sitl = None
        self._version = self._capture_id = None
        # Make sure no orphaned SITL keeps the TCP port bound.
        try:
            self._wsl("pkill -f betaflight_SITL", capture=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    def start(self, capture_id: str, version: str, dump_text: str) -> SitlStatus:
        """Load a capture into a fresh SITL instance and expose it for the Configurator."""
        if not version:
            raise SitlError("capture has no firmware version — cannot pick a SITL build")
        self._check_wsl()
        if not self.binary_available(version):
            raise SitlError(
                f"no SITL binary for firmware {version}; build it once with "
                f"`bash scripts/build_sitl.sh {version}` (inside WSL)"
            )

        # Only one session at a time.
        self.stop()

        host = "127.0.0.1"
        tcp = self.s.sitl_tcp_port
        elf = self._binary_path(version)
        run_dir = f"{self.s.sitl_run_dir}/{capture_id}"

        # Phase 1: fresh eeprom, load the dump, save (SITL writes eeprom and exits).
        loader = self._wsl(f"mkdir -p {run_dir} && cd {run_dir} && rm -f eeprom.bin "
                           f"&& exec {elf} >/dev/null 2>&1")
        try:
            if not _wait_port(host, tcp, timeout=self.s.sitl_boot_timeout):
                raise SitlError("SITL did not start (load phase)")
            load_dump_over_cli(host, tcp, dump_text)
        finally:
            # `save` reboots SITL; wait for the process/port to go away.
            if loader.poll() is None:
                try:
                    loader.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    loader.terminate()
        # Give the OS a moment to release the port after the reboot/exit.
        for _ in range(20):
            if _port_free(host, tcp):
                break
            time.sleep(0.3)

        # Phase 2: relaunch from the populated eeprom; this is the session SITL.
        self._sitl = self._wsl(f"cd {run_dir} && exec {elf} >/dev/null 2>&1")
        if not _wait_port(host, tcp, timeout=self.s.sitl_boot_timeout):
            self.stop()
            raise SitlError("SITL did not start (serve phase)")

        # websockify proxy so the WebSocket-only web Configurator can connect.
        self._proxy = subprocess.Popen(
            [sys.executable, "-m", "websockify",
             f"127.0.0.1:{self.s.sitl_ws_port}", f"127.0.0.1:{tcp}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_port(host, self.s.sitl_ws_port, timeout=10):
            self.stop()
            raise SitlError("websockify proxy did not start (is the 'websockify' package installed?)")

        self._version = version
        self._capture_id = capture_id
        return self.status()
