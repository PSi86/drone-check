"""Serial transport abstraction.

The rest of the code talks to a flight controller through a small byte-stream
interface so the real serial port and offline test doubles share one code path.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Protocol


class Transport(Protocol):
    """Minimal byte-stream interface used by MSP and the CLI session."""

    def write(self, data: bytes) -> None: ...

    def read(self, size: int) -> bytes:
        """Read up to ``size`` bytes; may return fewer (including zero)."""
        ...

    def close(self) -> None: ...


class SerialTransport:
    """Transport backed by a real USB serial port (pyserial)."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 0.2,
        connect_delay: float = 0.3,
    ):
        import serial  # imported lazily so tests don't require pyserial

        # dsrdtr/rtscts left at defaults; a normal FC VCP needs no flow control.
        self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        try:
            self._serial.dtr = True  # some CDC stacks only stream once DTR is set
        except Exception:
            pass
        # Give the USB CDC interface time to settle after open before talking.
        time.sleep(connect_delay)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def write(self, data: bytes) -> None:
        self._serial.write(data)
        self._serial.flush()

    def read(self, size: int) -> bytes:
        return self._serial.read(size)

    def close(self) -> None:
        try:
            self._serial.close()
        except Exception:
            pass


class SocketTransport:
    """Transport backed by an already-connected TCP socket.

    Used to drive a SITL instance's CLI/MSP over TCP (UART1 on 127.0.0.1:5761)
    with the same :class:`CliSession` logic the real serial path uses. ``read``
    returns whatever arrived within ``timeout`` (possibly empty), matching the
    pyserial-style contract that :func:`read_until` expects.
    """

    def __init__(self, sock, timeout: float = 0.2):
        import socket as _socket

        self._socket_mod = _socket
        self._sock = sock
        self._sock.settimeout(timeout)

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def read(self, size: int) -> bytes:
        try:
            return self._sock.recv(size)
        except (self._socket_mod.timeout, BlockingIOError):
            return b""
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class LoggingTransport:
    """Wraps a transport and tees all traffic to a debug file.

    Invaluable during the first hardware bring-up: every byte written to and read
    from the flight controller is recorded with a direction marker so a failed
    handshake can be diagnosed after the fact.
    """

    def __init__(self, inner: Transport, path: Path):
        self._inner = inner
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("wb")

    def write(self, data: bytes) -> None:
        self._fh.write(b"\n>>> TX " + repr(data).encode() + b"\n")
        self._fh.flush()
        self._inner.write(data)

    def read(self, size: int) -> bytes:
        data = self._inner.read(size)
        if data:
            self._fh.write(b"<<< RX " + repr(data).encode() + b"\n")
            self._fh.flush()
        return data

    def close(self) -> None:
        try:
            self._fh.close()
        finally:
            self._inner.close()


def drain(transport: Transport, duration: float = 0.2) -> bytes:
    """Read and discard whatever is buffered for ``duration`` seconds."""
    deadline = time.monotonic() + duration
    junk = bytearray()
    while time.monotonic() < deadline:
        chunk = transport.read(256)
        if chunk:
            junk.extend(chunk)
        else:
            time.sleep(0.01)
    return bytes(junk)


def read_until(
    transport: Transport,
    terminator: bytes,
    idle_timeout: float = 1.5,
    max_wait: float = 30.0,
    settle: float = 0.15,
) -> bytes:
    """Read until the stream *ends with* ``terminator`` (the CLI prompt).

    The CLI prompt ``# `` cannot be detected by substring match because
    ``diff all`` output is full of ``# ``-prefixed comment lines. The real
    prompt is the only place the stream *ends* with ``# `` and then the flight
    controller waits for input — so we end the read when the buffer ends with
    the terminator and a short ``settle`` read confirms nothing more is coming.
    A gap longer than ``idle_timeout`` (or ``max_wait`` overall) also ends it,
    which copes with large, slowly streamed output without a fixed deadline.
    """
    start = time.monotonic()
    last = start
    buf = bytearray()
    while True:
        chunk = transport.read(256)
        now = time.monotonic()
        if chunk:
            buf.extend(chunk)
            last = now
            if buf.endswith(terminator):
                confirm = drain(transport, settle)
                if not confirm:
                    break
                buf.extend(confirm)
                last = time.monotonic()
        else:
            if now - last > idle_timeout:
                break
            if now - start > max_wait:
                break
            time.sleep(0.01)
    return bytes(buf)
