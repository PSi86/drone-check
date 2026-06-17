"""A byte-level flight-controller simulator used as a Transport.

Unlike FakeFlightController (which bypasses I/O), this speaks the real wire
protocol: it parses ``$M<`` MSP request frames and replies with valid ``$M>``
frames, and once it receives ``#`` it behaves like the text CLI, echoing
commands and ending each response with the ``# `` prompt. This exercises the
MSP framing/CRC code, the CLI prompt detection and read_until end-to-end.
"""

from __future__ import annotations

from drone_check.demo import BETAFLIGHT_DUMP, BETAFLIGHT_STATUS


def _crc(size: int, cmd: int, payload: bytes) -> int:
    crc = size ^ cmd
    for b in payload:
        crc ^= b
    return crc & 0xFF


# Canned MSP v1 response payloads, keyed by command id.
_MSP_PAYLOADS = {
    1: bytes([0, 1, 46]),                       # API_VERSION -> 1.46
    2: b"BTFL",                                  # FC_VARIANT
    3: bytes([4, 5, 1]),                         # FC_VERSION -> 4.5.1
    4: b"S405" + bytes([0, 0, 8]) + b"HBFCS405",  # BOARD_INFO
    5: b"Dec 19 2024" + b"12:34:56" + b"77d01ba",  # BUILD_INFO (11+8+7)
    160: bytes(range(12)),                       # UID (3x uint32)
}


class FakeSerialFC:
    """In-memory Transport that emulates a Betaflight FC (MSP + CLI)."""

    def __init__(self, cli_outputs: dict[str, str] | None = None):
        self._in = bytearray()
        self._out = bytearray()
        self._in_cli = False
        self._cli = cli_outputs or {
            "version": BETAFLIGHT_DUMP.splitlines()[1],
            "dump all": BETAFLIGHT_DUMP.replace("\n", "\r\n"),
            "status": BETAFLIGHT_STATUS.replace("\n", "\r\n"),
        }

    # -- Transport interface ---------------------------------------------

    def write(self, data: bytes) -> None:
        self._in.extend(data)
        self._process()

    def read(self, size: int) -> bytes:
        chunk = bytes(self._out[:size])
        del self._out[: len(chunk)]
        return chunk

    def close(self) -> None:
        pass

    # -- emulation -------------------------------------------------------

    def _process(self) -> None:
        while self._in:
            if self._in_cli:
                if not self._process_cli_line():
                    return
            elif self._in[:1] == b"#":
                # Enter CLI mode; drop the '#' and one optional newline.
                del self._in[0]
                if self._in[:1] in (b"\r", b"\n"):
                    del self._in[0]
                self._in_cli = True
                self._emit(
                    "\r\nEntering CLI Mode, type 'exit' to return, or 'help'\r\n# "
                )
            elif self._in[:3] == b"$M<":
                if not self._process_msp_frame():
                    return
            else:
                del self._in[0]  # discard noise byte

    def _process_msp_frame(self) -> bool:
        if len(self._in) < 5:
            return False
        size = self._in[3]
        cmd = self._in[4]
        total = 5 + size + 1
        if len(self._in) < total:
            return False
        del self._in[:total]
        payload = _MSP_PAYLOADS.get(cmd, b"")
        frame = bytearray(b"$M>")
        frame.append(len(payload))
        frame.append(cmd)
        frame.extend(payload)
        frame.append(_crc(len(payload), cmd, payload))
        self._out.extend(frame)
        return True

    def _process_cli_line(self) -> bool:
        # A command line is terminated by CR (the client sends "<cmd>\r").
        if b"\r" not in self._in:
            return False
        idx = self._in.index(b"\r")
        line = bytes(self._in[:idx]).decode("ascii", "replace").strip()
        del self._in[: idx + 1]
        if line == "exit":
            self._emit("\r\nRebooting")
            self._in_cli = False
            return bool(self._in)
        body = self._cli.get(line, f"Unknown command: {line}")
        self._emit(f"{line}\r\n{body}\r\n# ")
        return bool(self._in)

    def _emit(self, text: str) -> None:
        self._out.extend(text.encode("ascii", "replace"))
