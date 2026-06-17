"""Minimal MSP (MultiWii Serial Protocol) client.

Only the handful of identification commands the tool needs are implemented, all
of which use MSP v1 framing::

    request : '$' 'M' '<' <size> <cmd> <payload...> <crc>
    response: '$' 'M' '>' <size> <cmd> <payload...> <crc>
    error   : '$' 'M' '!' ...
    crc     : XOR over <size>, <cmd> and every payload byte

The MSP and CLI interfaces share the same serial port, so we query MSP first
(machine-readable) and only then drop into the text CLI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .transport import Transport, drain

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_BUILD_INFO = 5
MSP_UID = 160


class MspError(Exception):
    """Raised on framing errors, CRC mismatch or timeout."""


@dataclass
class MspIdentity:
    api_version: str = ""
    variant: str = ""
    version: str = ""
    board_name: str = ""
    build_date: str = ""
    build_time: str = ""
    git_hash: str = ""
    uid: str = ""


def _is_printable(data: bytes) -> bool:
    return all(32 <= b < 127 for b in data)


def _crc(size: int, cmd: int, payload: bytes) -> int:
    crc = size ^ cmd
    for b in payload:
        crc ^= b
    return crc & 0xFF


class MspClient:
    def __init__(self, transport: Transport, timeout: float = 1.5, retries: int = 2):
        self._t = transport
        self._timeout = timeout
        self._retries = retries

    def _request(self, cmd: int, payload: bytes = b"") -> bytes:
        """Send a request and read the response, retrying on transient errors.

        Real USB links occasionally drop or corrupt the first exchange after the
        port opens, so a couple of retries (each draining stale bytes first)
        makes identification reliable.
        """
        frame = bytearray(b"$M<")
        frame.append(len(payload))
        frame.append(cmd)
        frame.extend(payload)
        frame.append(_crc(len(payload), cmd, payload))

        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            if attempt:
                drain(self._t, 0.1)
            self._t.write(bytes(frame))
            try:
                return self._read_response(cmd)
            except MspError as exc:
                last_error = exc
        raise last_error if last_error else MspError(f"MSP request {cmd} failed")

    def _read_response(self, expected_cmd: int) -> bytes:
        """Read and validate a single MSP v1 response frame."""
        deadline = time.monotonic() + self._timeout
        buf = bytearray()

        def need(n: int) -> bytes:
            while len(buf) < n:
                if time.monotonic() > deadline:
                    raise MspError(f"MSP timeout waiting for cmd {expected_cmd}")
                chunk = self._t.read(n - len(buf))
                if chunk:
                    buf.extend(chunk)
                else:
                    time.sleep(0.005)
            return bytes(buf[:n])

        # Resynchronise on the '$M' header in case of leftover bytes.
        while True:
            need(3)
            if buf[0:3] == b"$M>":
                break
            if buf[0:3] == b"$M!":
                raise MspError(f"MSP error response for cmd {expected_cmd}")
            del buf[0]  # drop one byte and retry header detection

        need(5)  # header(3) + size + cmd
        size = buf[3]
        cmd = buf[4]
        need(5 + size + 1)  # + payload + crc
        payload = bytes(buf[5 : 5 + size])
        crc = buf[5 + size]
        if crc != _crc(size, cmd, payload):
            raise MspError(f"MSP CRC mismatch for cmd {cmd}")
        if cmd != expected_cmd:
            raise MspError(f"MSP cmd mismatch: got {cmd}, expected {expected_cmd}")
        return payload

    # -- typed queries ----------------------------------------------------

    def api_version(self) -> str:
        p = self._request(MSP_API_VERSION)
        if len(p) < 3:
            raise MspError("MSP_API_VERSION payload too short")
        return f"{p[1]}.{p[2]}"

    def fc_variant(self) -> str:
        p = self._request(MSP_FC_VARIANT)
        if len(p) < 4:
            raise MspError("MSP_FC_VARIANT payload too short")
        return p[:4].decode("ascii", "replace")

    def fc_version(self) -> str:
        p = self._request(MSP_FC_VERSION)
        if len(p) < 3:
            raise MspError("MSP_FC_VERSION payload too short")
        return f"{p[0]}.{p[1]}.{p[2]}"

    def board_name(self) -> str:
        p = self._request(MSP_BOARD_INFO)
        # Layout varies a lot across firmware versions:
        #   boardId(4), hwRevision(2), [boardType(1)], [len(1) + name], ...
        # so we treat it as best-effort: try the length-prefixed name and only
        # accept it if it looks like a printable identifier, else fall back to
        # the 4-char board identifier. The authoritative board name comes from
        # the CLI anyway.
        if len(p) >= 8:
            name_len = p[6]
            name = p[7 : 7 + name_len]
            if 0 < name_len <= 32 and len(name) == name_len and _is_printable(name):
                return name.decode("ascii", "replace")
        return p[:4].decode("ascii", "replace").strip("\x00") if len(p) >= 4 else ""

    def build_info(self) -> tuple[str, str, str]:
        p = self._request(MSP_BUILD_INFO)
        if len(p) < 26:
            raise MspError("MSP_BUILD_INFO payload too short")
        date = p[0:11].decode("ascii", "replace").strip("\x00").strip()
        clock = p[11:19].decode("ascii", "replace").strip("\x00").strip()
        git = p[19:26].decode("ascii", "replace").strip("\x00").strip()
        return date, clock, git

    def uid(self) -> str:
        p = self._request(MSP_UID)
        if len(p) < 12:
            raise MspError("MSP_UID payload too short")
        # Three little-endian uint32 words -> a stable 96-bit hex identifier.
        words = [int.from_bytes(p[i : i + 4], "little") for i in (0, 4, 8)]
        return "".join(f"{w:08x}" for w in words)

    def identify(self) -> MspIdentity:
        """Query every identity field, validating completeness as we go."""
        ident = MspIdentity()
        ident.api_version = self.api_version()
        ident.variant = self.fc_variant()
        ident.version = self.fc_version()
        # Board info layout is version-dependent and non-critical (the CLI gives
        # the authoritative board name); never let it abort identification.
        try:
            ident.board_name = self.board_name()
        except MspError:
            ident.board_name = ""
        ident.build_date, ident.build_time, ident.git_hash = self.build_info()
        ident.uid = self.uid()
        if not ident.uid:
            raise MspError("flight controller did not report a UID")
        return ident
