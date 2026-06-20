"""Send MSP commands to an endpoint and collect raw responses (BFCD-009).

The probe is the reusable client behind the golden tests and bring-up: point it
at any MSP-speaking endpoint (a SITL UART over TCP today, the bf-configd
WebSocket bridge later) via a :class:`~drone_check.transport.Transport`, hand it
a list of commands, and get back the raw response payloads to snapshot or
compare. It deliberately captures *raw bytes* — semantic decoding is out of
scope; the golden comparison works on payloads directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..transport import Transport
from .commands import COMMANDS, MspCommand
from .msp import BfcdMspError, MspFrame, encode_request, try_decode


@dataclass
class ProbeResult:
    """The outcome of one MSP exchange."""

    command: str
    code: int
    ok: bool                 # a valid, non-error reply was received
    payload: bytes = b""
    error: str = ""          # populated when ok is False


class MspProbe:
    """Drives MSP request/response exchanges over a byte-stream transport."""

    def __init__(self, transport: Transport, timeout: float = 1.5):
        self._t = transport
        self._timeout = timeout

    def query(self, code: int, payload: bytes = b"", *, version: int = 1,
              flag: int = 0) -> MspFrame:
        """Send one request and return the decoded response frame.

        Raises :class:`BfcdMspError` on timeout or a frame that never validates.
        Resynchronises on the ``$`` magic so leftover bytes from a previous
        exchange cannot desync the parser.
        """
        self._t.write(encode_request(code, payload, version=version, flag=flag))
        deadline = time.monotonic() + self._timeout
        buf = bytearray()
        while True:
            if time.monotonic() > deadline:
                raise BfcdMspError(f"timeout waiting for reply to MSP cmd {code}")
            chunk = self._t.read(256)
            if not chunk:
                time.sleep(0.005)
                continue
            buf.extend(chunk)
            # Try to parse a frame from the front, dropping leading garbage.
            while buf:
                try:
                    frame, consumed = try_decode(bytes(buf))
                except BfcdMspError:
                    del buf[0]  # corrupt header/CRC: drop a byte and resync
                    continue
                if frame is None:
                    break  # need more bytes
                del buf[:consumed]
                if frame.cmd == code or not frame.ok:
                    return frame
                # A stale reply for a different command — keep reading.
            # fall through to read more

    def run(self, commands: "list[MspCommand] | None" = None) -> list[ProbeResult]:
        """Run a list of commands (default: the whole non-blocked matrix).

        Each command is attempted independently; a failure is recorded as a
        non-ok :class:`ProbeResult` rather than aborting the run, so one
        unsupported command never hides the rest.
        """
        cmds = commands if commands is not None else [
            c for c in COMMANDS if c.handling.value != "blocked"]
        results: list[ProbeResult] = []
        for c in cmds:
            version = 2 if c.code > 0xFF else 1
            try:
                frame = self.query(c.code, version=version)
            except BfcdMspError as exc:
                results.append(ProbeResult(c.name, c.code, ok=False, error=str(exc)))
                continue
            results.append(ProbeResult(c.name, c.code, ok=frame.ok,
                                       payload=frame.payload,
                                       error="" if frame.ok else "MSP error reply"))
        return results
