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
* The dump is fed through whichever CLI dialect the firmware uses — the legacy
  raw ``#`` prompt (Betaflight < 4.5.4 / INAV) or the framed MSP-CLI (Betaflight
  >= 4.5.4 / 2025.x, which ignores the raw ``#`` byte). See
  :func:`drone_check.cli_session.supports_framed_cli`.

The SITL binaries built by ``scripts/build_sitl.sh`` re-enable the VTX config
table, so ``vtxtable`` power values/labels are visible in this view too;
drone-check's own dump analysis remains the authoritative source for VTX power.
"""

from __future__ import annotations

import base64
import shlex
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .cli_session import (
    CliError,
    CliSession,
    ETX,
    LF,
    STX,
    supports_framed_cli,
)
from .config import Settings
from .transport import SocketTransport

# Spawn long-lived children (the WSL/SITL process and the websockify proxy) in
# their own process group on Windows. Otherwise they share drone-check's console
# process group and intercept/interfere with the console Ctrl+C — which broke the
# server's graceful shutdown after a SITL session had run, and lingering orphans
# kept breaking it across restarts. A new group never receives the console
# Ctrl+C; we always stop these children explicitly via terminate()/pkill anyway.
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class SitlError(RuntimeError):
    """A SITL session could not be started; the reason is operator-facing."""


class SitlCancelled(SitlError):
    """A start() was superseded by a stop() or a newer start() and bailed out.

    Not a real failure — the operator (or a newer request) asked for something
    else, so the in-flight start simply stops without resurrecting any state.
    """


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


def _connect_cli(host: str, port: int, attempts: int = 4) -> socket.socket:
    """Open a TCP connection to SITL's UART1 and enter CLI mode, with retries.

    Returns a socket already in CLI mode (the ``# `` prompt was seen). Right
    after a cold WSL boot the localhost relay may reset the first connection;
    each attempt reconnects from scratch, which is safe because the dump has
    not been sent or saved yet.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.sendall(b"#\r\n")  # enter CLI mode
            time.sleep(0.4)
            reply = _drain(sock, 1.5)
            if b"#" in reply or b"CLI" in reply:
                return sock
            # Connected but no CLI prompt yet (SITL still booting): retry.
            sock.close()
            last_exc = SitlError("SITL CLI did not respond with a prompt")
        except OSError as exc:
            last_exc = exc
        time.sleep(0.5 * (attempt + 1))
    raise SitlError(f"could not open SITL CLI on {host}:{port}: {last_exc}")


def _dump_commands(dump_text: str) -> list[str]:
    """Filter a ``dump all`` to the config commands SITL should replay.

    Drops comments and ``batch``/``save`` framing (we drive ``save`` ourselves)
    and the hardware pin/peripheral maps SITL always rejects (``resource`` /
    ``timer`` / ``dma`` — no GPIO on a host target). Skipping the latter changes
    nothing in the loaded config and shaves time off the load.
    """
    cmds = []
    for raw in dump_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in ("batch start", "batch end", "save"):
            continue
        if line.startswith(("resource ", "timer ", "dma ")):
            continue
        cmds.append(line)
    return cmds


def load_dump_over_cli(host: str, port: int, dump_text: str,
                       framed: bool = False, progress_cb=None) -> None:
    """Replay a ``dump all`` into SITL's CLI and persist it with ``save``.

    Two CLI dialects exist, exactly as on real hardware (see
    :func:`drone_check.cli_session.supports_framed_cli`):

    * legacy raw ``#`` prompt (Betaflight < 4.5.4 / INAV) — enter CLI, send the
      commands, ``save``;
    * framed MSP-CLI (Betaflight >= 4.5.4 / 2025.x) — newer firmware ignores the
      raw ``#`` byte, so each command is an ``STX..ETX`` frame instead.

    ``progress_cb(sent, total)`` (if given) is called as the load proceeds.
    """
    cmds = _dump_commands(dump_text)
    if framed:
        _load_dump_framed(host, port, cmds, progress_cb)
    else:
        _load_dump_legacy(host, port, cmds, progress_cb)


def _load_dump_legacy(host: str, port: int, cmds: list[str], progress_cb=None) -> None:
    """Load a dump via the legacy raw-``#`` CLI (Betaflight < 4.5.4 / INAV).

    Lines are sent in chunks well under SITL's 1400-byte RX buffer, draining the
    echo between chunks for flow control.
    """
    total = len(cmds)

    # Establish the CLI session with a few retries: right after a cold WSL boot
    # the WSL2 localhost relay can reset the very first connection (WinError
    # 10053 / connection reset) before SITL's CLI is reachable. Retrying the
    # handshake is safe because nothing has been sent or saved yet.
    sock = _connect_cli(host, port)
    try:
        # Send a chunk, then wait for its echo before sending the next: the echo
        # proves SITL's CLI task drained the RX buffer, so the outstanding bytes
        # never exceed one chunk (kept well under the 1400-byte RX buffer — sending
        # the whole dump at once would overflow that ring buffer and corrupt the
        # config silently). With SITL's poll timeout lowered to 10 ms the echo is
        # prompt, so this is close to the firmware's true processing rate.
        i = 0
        chunk_limit = 1024  # < 1400-byte RX buffer
        while i < total:
            chunk = bytearray()
            while i < total and len(chunk) < chunk_limit:
                chunk += cmds[i].encode("utf-8", "replace") + b"\r\n"
                i += 1
            sock.sendall(chunk)
            _drain(sock, 0.5, idle=0.02)
            if progress_cb is not None:
                progress_cb(i, total)

        _drain(sock, 1.0)
        sock.sendall(b"save\r\n")
        _drain(sock, 3.0)
    finally:
        sock.close()


def _connect_framed(host: str, port: int, attempts: int = 4) -> socket.socket:
    """Open a TCP connection to SITL's framed MSP-CLI, validating the link.

    No CLI mode is entered (newer firmware ignores the raw ``#`` byte). A
    read-only ``version`` probe per attempt confirms the framed CLI is actually
    answering before any config is sent — so a cold-WSL first-connection reset
    can't silently drop a setting. Returns a socket ready for the load.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=5)
            cli = CliSession(SocketTransport(sock), idle_timeout=1.0, max_wait=8.0)
            cli.command_framed("version")  # raises CliError if no ETX reply
            sock.settimeout(0.2)
            return sock
        except (OSError, CliError) as exc:
            last_exc = exc
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        time.sleep(0.5 * (attempt + 1))
    raise SitlError(f"could not open SITL framed CLI on {host}:{port}: {last_exc}")


def _read_etx_acks(sock: socket.socket, n: int,
                   total: float = 15.0, idle: float = 3.0) -> int:
    """Read framed replies until ``n`` ETX terminators have been seen.

    Each framed CLI command — even one SITL rejects — answers with exactly one
    ``STX..ETX`` frame, so counting ETX bytes counts *processed* commands. Returns
    the number actually seen (``< n`` only if it timed out)."""
    sock.setblocking(False)
    seen = 0
    start = last = time.monotonic()
    while seen < n:
        now = time.monotonic()
        if now - start > total or now - last > idle:
            break
        try:
            chunk = sock.recv(8192)
            if chunk:
                seen += chunk.count(ETX)
                last = now
            else:
                time.sleep(0.005)
        except BlockingIOError:
            time.sleep(0.005)
    return seen


def _load_dump_framed(host: str, port: int, cmds: list[str], progress_cb=None) -> None:
    """Load a dump via the framed MSP-CLI (Betaflight >= 4.5.4 / 2025.x).

    SITL's framed CLI runs *every* LF-separated line inside one ``STX..ETX``
    frame and answers with a single closing ETX, so we pack many commands into
    each frame (kept under SITL's 1400-byte RX ring, which has no backpressure
    and silently overwrites unread bytes) and wait for that one ETX before
    sending the next frame. Waiting for the ETX is true flow control — the frame
    is fully processed before the next is sent, so the ring never overruns and
    no config line is silently lost.

    Batching is what makes this fast: one command per frame is correct but ~30x
    slower, because each frame is gated by SITL's MSP-poll cadence (~20 ms) — a
    ~1200-line dump then takes ~25 s, versus ~1 s when batched. ``save`` reboots
    before it can send its closing ETX, so it is sent without waiting; the caller
    waits for the process to exit.
    """
    total = len(cmds)
    sock = _connect_framed(host, port)
    try:
        i = 0
        frame_limit = 1024  # < 1400-byte RX buffer, incl. STX/LF/ETX framing
        while i < total:
            frame = bytearray(STX)
            while i < total:
                line = cmds[i].encode("utf-8", "replace") + LF
                # Leave room for the closing ETX; always include at least one
                # line even if it alone would exceed the soft limit.
                if len(frame) + len(line) + 1 > frame_limit and len(frame) > 1:
                    break
                frame += line
                i += 1
            frame += ETX
            sock.sendall(frame)
            # One closing ETX per frame proves SITL ran the whole batch.
            _read_etx_acks(sock, 1)
            if progress_cb is not None:
                progress_cb(i, total)

        sock.sendall(STX + b"save" + LF + ETX)
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


def _speaks_msp(host: str, port: int) -> bool:
    """True if ``port`` answers an MSP request (an MSP-capable serial port).

    SITL maps each UART to TCP ``5760 + n``; the CLI/MSP can live on whichever
    UART the loaded config assigns ``MSP`` to (not necessarily UART1). We probe
    with ``MSP_API_VERSION`` and check for an MSP reply header (``$M>`` for v1,
    ``$X>`` for v2)."""
    try:
        with socket.create_connection((host, port), timeout=1) as s:
            s.sendall(b"$M<\x00\x01\x01")  # MSP_API_VERSION, no payload
            s.settimeout(0.6)
            try:
                reply = s.recv(64)
            except OSError:
                return False
            return reply.startswith(b"$M>") or reply.startswith(b"$X>")
    except OSError:
        return False


def _find_msp_port(host: str, base_port: int, count: int, timeout: float) -> int | None:
    """Wait for the serve-phase SITL to come up and return the UART TCP port that
    speaks MSP, so the proxy targets it wherever the loaded config put MSP.

    ``base_port`` is UART1's TCP port; ``count`` UARTs are scanned (UART1..UARTn).
    UART1 is tried first so the common case (MSP on UART1) is picked immediately.
    """
    end = time.monotonic() + timeout
    ports = [base_port + i for i in range(count)]
    while time.monotonic() < end:
        for p in ports:
            if _speaks_msp(host, p):
                return p
        time.sleep(0.3)
    return None


# ---- session ----------------------------------------------------------------


@dataclass
class SitlStatus:
    running: bool
    starting: bool = False
    phase: str = "idle"  # checking|starting|loading|saving|starting2|proxy|ready|idle
    detail: str = ""
    sent: int = 0
    total: int = 0
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
        # Progress, updated from the worker thread and read by status().
        self._lock = threading.Lock()
        self._phase = "idle"
        self._detail = ""
        self._sent = 0
        self._total = 0
        self._starting = False
        # Lifecycle generation: every start() claims a generation; stop() (or a
        # newer start()) bumps it. A long-running start() in a background thread
        # checks its generation between steps and bails the moment it is
        # superseded, so a concurrent stop() reliably wins instead of the
        # in-flight start() resurrecting the "starting" state. (See _claim_gen.)
        self._gen = 0
        # WSL ownership: drone-check starts WSL lazily on the first session and
        # only terminates it on shutdown if it was the one to bring it up — never
        # killing a WSL the operator already had running for other work.
        self._wsl_ownership_checked = False
        self._we_started_wsl = False
        # Whether a SITL session was ever started this run. If not, shutdown() is
        # a no-op so it never cold-starts WSL just to pkill nothing.
        self._started_once = False
        # Memoized "is WSL + the configured distro present" (checked without
        # booting the VM); gates whether the Configurator feature is offered.
        self._wsl_ok: bool | None = None

    def _progress(self, phase: str, detail: str = "", sent: int = 0,
                  total: int = 0, starting: bool = True,
                  gen: int | None = None) -> bool:
        """Update the progress state. When ``gen`` is given, the update is
        applied only if that generation is still current; returns whether it
        was applied. This lets a superseded start() stop touching the state
        atomically, so a concurrent stop() is not undone."""
        with self._lock:
            if gen is not None and self._gen != gen:
                return False
            self._phase = phase
            self._detail = detail
            self._sent = sent
            self._total = total
            self._starting = starting
            return True

    # -- helpers ----------------------------------------------------------

    def _wsl(self, script: str, *, capture: bool = False, timeout: float | None = None):
        cmd = ["wsl", "-d", self.s.sitl_distro, "--", "bash", "-lc", script]
        if capture:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        # Detach stdin (an inherited console stdin is unusable under the web
        # server and can block startup) and put the child in its own process
        # group so it does not swallow the console Ctrl+C.
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=_NEW_PROCESS_GROUP)

    def _wsl_b64(self, script: str, *, capture: bool = False, timeout: float | None = None):
        """Run a bash script in WSL, passed base64-encoded so its quoting, globs
        and ``$(...)`` survive the Windows → wsl.exe → bash command-line round-trip
        intact (the raw arg form mangles embedded quotes). Use for any non-trivial
        script."""
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        return self._wsl(f"echo {b64} | base64 -d | bash", capture=capture, timeout=timeout)

    def _binary_path(self, version: str) -> str:
        # WSL path; ~ expands inside `bash -lc`.
        return f"{self.s.sitl_cache_dir}/{version}/betaflight_SITL.elf"

    def _cache_valid(self, marker: str, elf: str) -> bool:
        """True if a capture's eeprom was already built and is newer than the binary."""
        try:
            res = self._wsl(f"test -f {marker} && test {marker} -nt {elf} && echo yes",
                            capture=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0 and "yes" in (res.stdout or "")

    def binary_available(self, version: str) -> bool:
        path = self._binary_path(version)
        try:
            res = self._wsl(f"test -f {path} && echo yes", capture=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return res.returncode == 0 and "yes" in (res.stdout or "")

    # -- distribution (list / package / install pre-built binaries) --------

    def _winpath_to_wsl(self, win_path: str) -> str:
        """Map a Windows path to its WSL path (works for not-yet-existing files)."""
        res = self._wsl_b64(f"wslpath -a {shlex.quote(win_path)}", capture=True, timeout=20)
        out = (res.stdout or "").strip()
        if res.returncode != 0 or not out:
            raise SitlError(f"could not map Windows path into WSL: {win_path}")
        return out

    def list_cache(self) -> list[dict]:
        """The SITL versions present in the WSL cache: ``[{version, bytes, static}]``."""
        self._check_wsl()
        cache = self.s.sitl_cache_dir
        script = (
            f'for d in {cache}/*/; do e="$d/betaflight_SITL.elf"; [ -f "$e" ] || continue; '
            f'if file "$e" | grep -q "statically linked"; then s=static; else s=dynamic; fi; '
            f'printf "%s\\t%s\\t%s\\n" "$(basename "$d")" "$(stat -c%s "$e")" "$s"; done'
        )
        res = self._wsl_b64(script, capture=True, timeout=30)
        items: list[dict] = []
        for line in (res.stdout or "").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 3:
                items.append({"version": parts[0], "bytes": int(parts[1]),
                              "static": parts[2] == "static"})
        return items

    def package_cache(self, out_win_path: str, versions: list[str]) -> str:
        """Bundle cached binaries (all, or the given versions) into a portable
        archive at the Windows path ``out_win_path``. Returns the script output."""
        self._check_wsl()
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "package_sitl.sh"
        if not script_path.is_file():
            raise SitlError(f"package script not found: {script_path}")
        wsl_script = self._winpath_to_wsl(str(script_path))
        wsl_out = self._winpath_to_wsl(out_win_path)
        args = " ".join(shlex.quote(v) for v in versions)
        res = self._wsl_b64(f"bash {shlex.quote(wsl_script)} {shlex.quote(wsl_out)} {args}",
                            capture=True, timeout=300)
        if res.returncode != 0:
            raise SitlError(f"packaging failed: {(res.stderr or res.stdout or '').strip()}")
        return (res.stdout or "").strip()

    def install_bundle(self, bundle_win_path: str) -> list[str]:
        """Install a bundle (created by ``package_cache``) into the WSL cache,
        verifying checksums. Returns the versions the bundle contained."""
        self._check_wsl()
        wsl_bundle = self._winpath_to_wsl(bundle_win_path)
        listing = self._wsl_b64(f"tar -tzf {shlex.quote(wsl_bundle)}", capture=True, timeout=60)
        if listing.returncode != 0:
            raise SitlError(f"cannot read bundle {bundle_win_path}: "
                            f"{(listing.stderr or '').strip()}")
        versions = sorted({
            ln.split("/")[1] for ln in (listing.stdout or "").splitlines()
            if ln.strip().endswith("betaflight_SITL.elf") and "/" in ln.strip().strip("./")
        })
        if not versions:
            raise SitlError(f"{bundle_win_path} is not a SITL bundle (no binaries inside)")
        cache = self.s.sitl_cache_dir
        # Extract into the cache, verify checksums, then drop the manifest files.
        script = (
            f"set -e; mkdir -p {cache}; tar -xzf {shlex.quote(wsl_bundle)} -C {cache}; "
            f"cd {cache}; sha256sum -c SHA256SUMS 1>&2; rm -f SHA256SUMS bundle-info.txt"
        )
        res = self._wsl_b64(script, capture=True, timeout=180)
        if res.returncode != 0:
            raise SitlError(f"install failed (checksum or extract): "
                            f"{(res.stderr or res.stdout or '').strip()}")
        return versions

    def _check_wsl(self) -> None:
        try:
            res = self._wsl("echo ok", capture=True, timeout=30)
        except FileNotFoundError as exc:
            raise SitlError("WSL not found — install WSL to use the Configurator view") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise SitlError(f"WSL not reachable: {exc}") from exc
        if res.returncode != 0 or "ok" not in (res.stdout or ""):
            raise SitlError(f"WSL distro '{self.s.sitl_distro}' not available")

    def wsl_available(self) -> bool:
        """Whether WSL is installed and the configured distro exists — checked
        WITHOUT booting the VM (``wsl -l -q`` only enumerates registered distros).

        Used to decide whether the "view in Configurator" feature can work at all,
        so the UI hides it on machines without WSL. ``wsl.exe`` missing (not
        installed) returns False rather than raising."""
        try:
            res = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        if res.returncode != 0:
            return False
        out = res.stdout or b""
        # wsl.exe prints UTF-16LE on Windows; fall back to utf-8 elsewhere.
        text = out.decode("utf-16-le", "ignore")
        if self.s.sitl_distro not in text:
            text = out.decode("utf-8", "ignore")
        names = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return self.s.sitl_distro in names

    def available(self) -> bool:
        """Whether the Configurator/SITL feature should be offered: enabled in
        config AND WSL present. The WSL probe is memoized (checked once) so the
        frequently-polled status endpoint doesn't re-run ``wsl -l -q`` each time."""
        if not self.s.sitl_enabled:
            return False
        if self._wsl_ok is None:
            self._wsl_ok = self.wsl_available()
        return self._wsl_ok

    def _wsl_running(self) -> bool:
        """Whether our distro is already running. Does NOT start it (``wsl -l
        --running`` only lists, it never boots the VM)."""
        try:
            res = subprocess.run(["wsl", "-l", "-q", "--running"],
                                 capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        out = res.stdout or b""
        # wsl.exe prints UTF-16LE on Windows; fall back to utf-8 elsewhere.
        text = out.decode("utf-16-le", "ignore")
        if self.s.sitl_distro not in text:
            text = out.decode("utf-8", "ignore")
        names = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return self.s.sitl_distro in names

    def _note_wsl_ownership(self) -> None:
        """On the first SITL start of this run, remember whether WSL was already
        running. If not, drone-check is bringing it up and must also tear it down
        on shutdown (see shutdown())."""
        if self._wsl_ownership_checked:
            return
        self._wsl_ownership_checked = True
        self._we_started_wsl = not self._wsl_running()

    def _wait_port_free(self, host: str, port: int,
                        attempts: int = 40, delay: float = 0.15) -> bool:
        """Wait (up to ``attempts * delay`` s) for ``port`` to become free."""
        for _ in range(attempts):
            if _port_free(host, port):
                return True
            time.sleep(delay)
        return False

    # -- lifecycle --------------------------------------------------------

    def status(self) -> SitlStatus:
        running = self._sitl is not None and self._sitl.poll() is None
        with self._lock:
            active = running or self._starting
            return SitlStatus(
                running=running,
                starting=self._starting,
                phase=self._phase,
                detail=self._detail,
                sent=self._sent,
                total=self._total,
                # Report which capture is involved while *starting* too, so the
                # UI can say what the "cancel" button would cancel.
                version=self._version if active else None,
                capture_id=self._capture_id if active else None,
                connect_url=f"ws://127.0.0.1:{self.s.sitl_ws_port}" if running else None,
            )

    def _claim_gen(self) -> int:
        """Start a new lifecycle generation and return its id."""
        with self._lock:
            self._gen += 1
            return self._gen

    def _is_current(self, gen: int) -> bool:
        with self._lock:
            return self._gen == gen

    def _teardown(self) -> None:
        """Kill the proxy + SITL and drop the handles. Does not touch the
        generation or progress — callers decide what state to report."""
        for proc in (self._proxy, self._sitl):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._proxy = self._sitl = None
        self._version = self._capture_id = None
        # Make sure no orphaned SITL keeps the TCP port bound. Use -x (exact
        # process name): -f would also match this very command's shell.
        try:
            self._wsl("pkill -x betaflight_SITL", capture=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass

    def stop(self) -> None:
        # Bump the generation first so any start() running in another thread
        # sees itself superseded and stops driving the progress state.
        # Note: this ends the SITL *session* only — it never stops WSL itself,
        # which stays up for the next "open in Configurator" until drone-check
        # exits (see shutdown()).
        self._claim_gen()
        self._teardown()
        self._progress("idle", "", starting=False)

    def shutdown(self) -> None:
        """Called once when drone-check exits: end the session and, if we were
        the ones who started WSL, terminate that distro too. Uses ``--terminate
        <distro>`` (not ``--shutdown``) so other distros, e.g. docker-desktop,
        keep running."""
        if not self._started_once:
            return  # SITL never ran — nothing to tear down, don't poke WSL
        self.stop()
        if self._we_started_wsl:
            try:
                subprocess.run(["wsl", "--terminate", self.s.sitl_distro],
                               capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
            self._we_started_wsl = False

    def _ensure_current(self, gen: int, loader: "subprocess.Popen | None" = None) -> None:
        """Abort the in-flight start() if its generation was superseded.

        A concurrent stop() (or a newer start()) bumps the generation; when that
        happens this start() must not keep launching processes or driving the
        progress state. Clean up what we created and raise so the caller bails.
        """
        if self._is_current(gen):
            return
        if loader is not None and loader.poll() is None:
            try:
                loader.terminate()
            except OSError:
                pass
        self._teardown()
        raise SitlCancelled("SITL start was cancelled")

    def start(self, capture_id: str, version: str, dump_text: str) -> SitlStatus:
        """Load a capture into a fresh SITL instance and expose it for the Configurator.

        Runs in a worker thread; a concurrent stop() (or a newer start()) bumps
        the lifecycle generation, so this bails cleanly as SitlCancelled instead
        of leaving the UI stuck on a transient "starting" state.
        """
        if not version:
            raise SitlError("capture has no firmware version — cannot pick a SITL build")
        # Claim a generation: this supersedes any previous in-flight start() and
        # lets us detect a stop() that arrives while we run in a worker thread.
        gen = self._claim_gen()
        try:
            return self._run_start(gen, capture_id, version, dump_text)
        except SitlCancelled:
            raise
        except Exception as exc:
            # If a stop()/newer start() superseded us, any error (e.g. the killed
            # SITL dropping the CLI connection mid-load) is just cancellation.
            if not self._is_current(gen):
                self._teardown()
                raise SitlCancelled("SITL start was cancelled") from exc
            # A genuine failure: clean up and surface a clear operator message
            # instead of letting an unexpected exception become an HTTP 500.
            self._teardown()
            self._progress("idle", "", starting=False, gen=gen)
            if isinstance(exc, SitlError):
                raise
            raise SitlError(f"SITL start failed: {exc}") from exc

    def _run_start(self, gen: int, capture_id: str, version: str,
                   dump_text: str) -> SitlStatus:
        self._started_once = True
        self._progress("checking", "Starting WSL and checking SITL binary…", gen=gen)
        # Before the first WSL call, record whether WSL was already running so we
        # only terminate it on shutdown if drone-check started it.
        self._note_wsl_ownership()
        self._check_wsl()
        if not self.binary_available(version):
            self._progress("idle", "", starting=False, gen=gen)
            raise SitlError(
                f"no SITL binary for firmware {version}; build it once with "
                f"`bash scripts/build_sitl.sh {version}` (inside WSL)"
            )

        # Only one session at a time: tear down the previous one. (We already own
        # the current generation, so use _teardown(), not stop(), which would
        # bump the generation and invalidate us.)
        self._ensure_current(gen)
        self._teardown()
        # Record what we are starting so status() can report it while starting
        # (teardown cleared these; set them after it).
        self._capture_id = capture_id
        self._version = version

        host = "127.0.0.1"
        tcp = self.s.sitl_tcp_port
        elf = self._binary_path(version)
        run_dir = f"{self.s.sitl_run_dir}/{capture_id}"
        marker = f"{run_dir}/.loaded"

        # Phase 1 is the slow part (feeding the whole dump through SITL's CLI), so
        # cache it per capture: once a capture's eeprom is built, future views skip
        # straight to serving. The cache is invalidated automatically when the SITL
        # binary is newer than the marker (e.g. after rebuilding with new features).
        if not self._cache_valid(marker, elf):
            self._ensure_current(gen)
            self._progress("starting", "Starting SITL (load phase)…", gen=gen)
            loader = self._wsl(f"rm -rf {run_dir} && mkdir -p {run_dir} && cd {run_dir} "
                               f"&& exec {elf} >/dev/null 2>&1")
            try:
                if not _wait_port(host, tcp, timeout=self.s.sitl_boot_timeout):
                    self._ensure_current(gen, loader)
                    self._teardown()
                    self._progress("idle", "", starting=False, gen=gen)
                    raise SitlError("SITL did not start (load phase)")
                self._ensure_current(gen, loader)
                self._progress("loading", "Loading configuration…", 0, 0, gen=gen)
                load_dump_over_cli(
                    host, tcp, dump_text,
                    framed=supports_framed_cli(version),
                    progress_cb=lambda sent, total: self._progress(
                        "loading", "Loading configuration…", sent, total, gen=gen),
                )
            finally:
                # `save` reboots SITL; wait for the process/port to go away.
                self._progress("saving", "Saving and rebooting SITL…",
                               self._sent, self._total, gen=gen)
                if loader.poll() is None:
                    try:
                        loader.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        loader.terminate()
            self._ensure_current(gen)
            # Give the OS a moment to release the port after the reboot/exit.
            if not self._wait_port_free(host, tcp):
                # The load-phase SITL didn't exit on its own (e.g. `save` didn't
                # trigger a clean reboot). The eeprom write is long finished by
                # now, so force it down and wait again — otherwise phase 2 cannot
                # bind the port and fails with "did not start (serve phase)".
                try:
                    self._wsl("pkill -x betaflight_SITL", capture=True, timeout=15)
                except (OSError, subprocess.SubprocessError):
                    pass
                self._wait_port_free(host, tcp)
            # Mark the eeprom as fully loaded so future views reuse it.
            try:
                self._wsl(f"touch {marker}", capture=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass

        # Phase 2: serve from the populated eeprom; this is the session SITL.
        self._ensure_current(gen)
        self._progress("starting2", "Starting SITL from saved configuration…", gen=gen)
        self._sitl = self._wsl(f"cd {run_dir} && exec {elf} >/dev/null 2>&1")
        # The loaded config decides which UART carries MSP, and SITL maps each
        # UART to its own TCP port (UART1=tcp, UART2=tcp+1, …). A capture can put
        # MSP on a UART other than UART1 (e.g. `serial UART6 ...` with the MSP
        # bit), so don't assume UART1 — find the port that actually answers MSP
        # and point the Configurator proxy at it. Otherwise the Configurator
        # connects to a non-MSP port and times out ("no configuration received").
        msp_port = _find_msp_port(host, tcp, count=8, timeout=self.s.sitl_boot_timeout)
        if msp_port is None:
            self._ensure_current(gen)
            self._teardown()
            self._progress("idle", "", starting=False, gen=gen)
            raise SitlError("SITL did not start (serve phase)")

        # websockify proxy so the WebSocket-only web Configurator can connect.
        self._ensure_current(gen)
        self._progress("proxy", "Starting Configurator proxy…", gen=gen)
        self._proxy = subprocess.Popen(
            [sys.executable, "-m", "websockify",
             f"127.0.0.1:{self.s.sitl_ws_port}", f"127.0.0.1:{msp_port}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_NEW_PROCESS_GROUP,
        )
        if not _wait_port(host, self.s.sitl_ws_port, timeout=10):
            self._ensure_current(gen)
            self._teardown()
            self._progress("idle", "", starting=False, gen=gen)
            raise SitlError("websockify proxy did not start (is the 'websockify' package installed?)")

        # Only publish the live session if we are still the current generation.
        self._ensure_current(gen)
        self._version = version
        self._capture_id = capture_id
        if not self._progress("ready", "Ready", starting=False, gen=gen):
            # A stop()/newer start() landed between the check and here; bail.
            self._teardown()
            raise SitlCancelled("SITL start was cancelled")
        return self.status()
