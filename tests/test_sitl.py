"""SITL dump-loading logic (no real WSL/SITL; sockets and _drain are faked)."""

import socket

import pytest

from drone_check import sitl
from drone_check.config import Settings


class FakeSock:
    def __init__(self):
        self.sent = bytearray()
        self.closed = False

    def sendall(self, data):
        self.sent += data

    def setblocking(self, _flag):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def fake_socket(monkeypatch):
    sock = FakeSock()
    monkeypatch.setattr(sitl.socket, "create_connection", lambda *a, **k: sock)
    # Skip the real read/wait loop; return a CLI prompt so the handshake in
    # _connect_cli is satisfied on the first attempt. We only care what we send.
    monkeypatch.setattr(sitl, "_drain", lambda *a, **k: b"\r\n# ")
    return sock


def test_load_dump_filters_framing_and_drives_save(fake_socket):
    dump = "\n".join([
        "# version",
        "# Betaflight ...",
        "batch start",
        "board_name BETAFPVF405",
        "resource MOTOR 1 B00",
        "timer A00 AF1",
        "dma pin A00 0",
        "set craft_name = U250_FPV",
        "set motor_pwm_protocol = DSHOT600",
        "save",
    ])
    sitl.load_dump_over_cli("127.0.0.1", 5761, dump)

    lines = fake_socket.sent.decode().splitlines()
    # CLI mode is entered first with a lone '#'.
    assert lines[0] == "#"
    body = lines[1:]
    assert "set craft_name = U250_FPV" in body
    assert "set motor_pwm_protocol = DSHOT600" in body
    assert "board_name BETAFPVF405" in body  # identity lines are sent as-is
    # The dump's own comments and batch framing are dropped...
    assert not any(ln.startswith("#") for ln in body)
    assert "batch start" not in body
    # ...and hardware pin/peripheral maps SITL rejects are skipped entirely.
    assert "resource MOTOR 1 B00" not in body
    assert "timer A00 AF1" not in body
    assert "dma pin A00 0" not in body
    # ...but we drive `save` exactly once at the end to persist + reboot.
    assert body.count("save") == 1
    assert body[-1] == "save"
    assert fake_socket.closed


class FramedFakeSock:
    """A SITL socket speaking the framed MSP-CLI: every STX..ETX frame written
    is acked with an ETX-terminated reply, so command_framed completes."""

    def __init__(self):
        self.sent = bytearray()
        self.closed = False
        self._inbox = bytearray()

    def sendall(self, data):
        self.sent += data
        if data.endswith(b"\x03"):  # a complete frame → reply with an ETX frame
            self._inbox += b"\x02ack\x03"

    def settimeout(self, _t):
        pass

    def setblocking(self, _f):
        pass

    def recv(self, size):
        if self._inbox:
            chunk = bytes(self._inbox[:size])
            del self._inbox[:size]
            return chunk
        raise socket.timeout()

    def close(self):
        self.closed = True


def test_load_dump_framed_uses_stx_etx_and_save(monkeypatch):
    sock = FramedFakeSock()
    monkeypatch.setattr(sitl.socket, "create_connection", lambda *a, **k: sock)
    monkeypatch.setattr(sitl.time, "sleep", lambda *_: None)

    dump = "\n".join([
        "# version",
        "batch start",
        "board_name BETAFPVF405",
        "resource MOTOR 1 B00",
        "timer A00 AF1",
        "dma pin A00 0",
        "set craft_name = U250_FPV",
        "save",
    ])
    sitl.load_dump_over_cli("127.0.0.1", 5761, dump, framed=True)

    sent = bytes(sock.sent)
    # The link is validated with a read-only framed `version` probe first.
    assert b"\x02version\x0a\x03" in sent
    # Real config lines are sent as STX..ETX frames, not raw newline commands.
    assert b"\x02set craft_name = U250_FPV\x0a\x03" in sent
    assert b"\x02board_name BETAFPVF405\x0a\x03" in sent
    # `save` is driven exactly once, as a frame, to persist + reboot.
    assert b"\x02save\x0a\x03" in sent
    # The framed CLI never enters raw `#` mode, and framing/comments/HW maps are
    # dropped just like the legacy path.
    assert b"#\r" not in sent
    assert b"resource" not in sent
    assert b"timer" not in sent
    assert b"batch start" not in sent
    assert sock.closed


def test_supports_framed_cli_picks_per_firmware_generation():
    from drone_check.cli_session import supports_framed_cli
    # Legacy raw-`#` firmware.
    assert supports_framed_cli("4.4.0") is False
    assert supports_framed_cli("4.5.0") is False
    assert supports_framed_cli("4.5.3") is False
    # Framed MSP-CLI firmware (>= 4.5.4 and the 2025.x year-based scheme).
    assert supports_framed_cli("4.5.4") is True
    assert supports_framed_cli("2025.12.2") is True
    # Unparseable / missing → fail safe to legacy.
    assert supports_framed_cli("") is False
    assert supports_framed_cli(None) is False


def test_connect_cli_retries_after_reset(monkeypatch):
    # The first connection is reset (as the cold-WSL localhost relay can do);
    # the second succeeds and reaches the CLI prompt.
    attempts = {"n": 0}

    def flaky_connect(*a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionResetError("relay reset")
        return FakeSock()

    monkeypatch.setattr(sitl.socket, "create_connection", flaky_connect)
    monkeypatch.setattr(sitl, "_drain", lambda *a, **k: b"\r\n# ")
    monkeypatch.setattr(sitl.time, "sleep", lambda *_: None)

    sock = sitl._connect_cli("127.0.0.1", 5761)
    assert attempts["n"] == 2
    assert sock.sent.startswith(b"#")


def test_connect_cli_fails_closed_after_attempts(monkeypatch):
    def always_reset(*a, **k):
        raise ConnectionAbortedError("aborted")

    monkeypatch.setattr(sitl.socket, "create_connection", always_reset)
    monkeypatch.setattr(sitl.time, "sleep", lambda *_: None)

    with pytest.raises(sitl.SitlError) as exc:
        sitl._connect_cli("127.0.0.1", 5761, attempts=3)
    assert "could not open SITL CLI" in str(exc.value)


def test_binary_available_false_without_wsl(monkeypatch):
    runner = sitl.SitlRunner(Settings())

    def boom(*a, **k):
        raise FileNotFoundError("no wsl")

    monkeypatch.setattr(runner, "_wsl", boom)
    assert runner.binary_available("4.4.0") is False


def test_start_fails_closed_when_binary_missing(monkeypatch):
    runner = sitl.SitlRunner(Settings())
    monkeypatch.setattr(runner, "_check_wsl", lambda: None)
    monkeypatch.setattr(runner, "binary_available", lambda v: False)

    with pytest.raises(sitl.SitlError) as exc:
        runner.start("cap", "4.4.0", "set x = 1\n")
    assert "build_sitl.sh 4.4.0" in str(exc.value)


def test_start_requires_version(monkeypatch):
    runner = sitl.SitlRunner(Settings())
    with pytest.raises(sitl.SitlError):
        runner.start("cap", "", "set x = 1\n")


def test_progress_ignores_superseded_generation():
    runner = sitl.SitlRunner(Settings())
    gen = runner._claim_gen()
    assert runner._progress("loading", starting=True, gen=gen) is True
    assert runner.status().starting is True
    runner._claim_gen()  # a stop()/newer start() supersedes `gen`
    # The stale update is dropped, so it cannot resurrect the "starting" state.
    assert runner._progress("loading", starting=True, gen=gen) is False


def test_note_wsl_ownership_marks_started_when_cold(monkeypatch):
    runner = sitl.SitlRunner(Settings())
    monkeypatch.setattr(runner, "_wsl_running", lambda: False)
    runner._note_wsl_ownership()
    assert runner._we_started_wsl is True
    # Idempotent: a later call does not flip the verdict.
    monkeypatch.setattr(runner, "_wsl_running", lambda: True)
    runner._note_wsl_ownership()
    assert runner._we_started_wsl is True


def test_note_wsl_ownership_leaves_preexisting_wsl_alone(monkeypatch):
    runner = sitl.SitlRunner(Settings())
    monkeypatch.setattr(runner, "_wsl_running", lambda: True)
    runner._note_wsl_ownership()
    assert runner._we_started_wsl is False


def test_shutdown_noop_when_sitl_never_started(monkeypatch):
    runner = sitl.SitlRunner(Settings())
    cmds = []
    monkeypatch.setattr(sitl.subprocess, "run",
                        lambda cmd, *a, **k: cmds.append(cmd))
    # SITL never ran this session → shutdown() must not poke WSL at all.
    runner.shutdown()
    assert cmds == []


def test_shutdown_terminates_wsl_only_if_we_started_it(monkeypatch):
    runner = sitl.SitlRunner(Settings())
    runner._started_once = True  # a SITL session ran this run
    monkeypatch.setattr(runner, "_teardown", lambda: None)
    cmds = []
    monkeypatch.setattr(sitl.subprocess, "run",
                        lambda cmd, *a, **k: cmds.append(cmd))

    # We did not start WSL → leave it running.
    runner._we_started_wsl = False
    runner.shutdown()
    assert not any("--terminate" in c for c in cmds)

    # We started WSL → terminate exactly our distro (not --shutdown).
    cmds.clear()
    runner._we_started_wsl = True
    runner.shutdown()
    terminate = [c for c in cmds if "--terminate" in c]
    assert terminate and terminate[0] == ["wsl", "--terminate", runner.s.sitl_distro]
    assert runner._we_started_wsl is False


def test_start_bails_when_stopped_midway(monkeypatch):
    # A concurrent stop() arrives while start() is still doing its WSL checks;
    # start() must bail with SitlCancelled and leave the state idle (not stuck
    # on "starting"), so the UI does not hang.
    runner = sitl.SitlRunner(Settings())
    monkeypatch.setattr(runner, "_check_wsl", lambda: runner.stop())
    monkeypatch.setattr(runner, "binary_available", lambda v: True)
    monkeypatch.setattr(runner, "_teardown", lambda: None)

    with pytest.raises(sitl.SitlCancelled):
        runner.start("cap", "4.4.0", "set x = 1\n")

    st = runner.status()
    assert st.starting is False
    assert st.running is False
    assert st.phase == "idle"
