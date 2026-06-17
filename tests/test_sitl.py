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
    # Skip the real read/wait loop — we only care about what we send.
    monkeypatch.setattr(sitl, "_drain", lambda *a, **k: b"")
    return sock


def test_load_dump_filters_framing_and_drives_save(fake_socket):
    dump = "\n".join([
        "# version",
        "# Betaflight ...",
        "batch start",
        "board_name BETAFPVF405",
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
    assert "board_name BETAFPVF405" in body  # resource/board lines are sent as-is
    # The dump's own comments and batch framing are dropped...
    assert not any(ln.startswith("#") for ln in body)
    assert "batch start" not in body
    # ...but we drive `save` exactly once at the end to persist + reboot.
    assert body.count("save") == 1
    assert body[-1] == "save"
    assert fake_socket.closed


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
