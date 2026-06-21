"""MSP frame codec for the probe and golden-test tooling (supports v1 and v2).

drone-check already has an MSP *client* (:mod:`drone_check.msp`) tuned for the
handful of identity commands it reads off real hardware over MSP v1. bf-configd
needs something broader and lower-level: encode an arbitrary request and decode
an arbitrary response, in **both** MSP v1 and v2 framing, so the probe tool can
send the whole command matrix and the golden tests can compare raw payloads
byte-for-byte against SITL. This module is that codec, deliberately standalone
and side-effect free so it is trivial to unit-test by round-trip.

MSP v1 frame::

    '$' 'M' <dir> <size:u8> <cmd:u8> <payload...> <crc>
    crc = XOR over size, cmd and every payload byte
    dir: '<' request, '>' response, '!' error

MSP v2 frame (needed for cmd >= 256 and large payloads)::

    '$' 'X' <dir> <flag:u8> <cmd:u16le> <size:u16le> <payload...> <crc>
    crc = CRC8/DVB-S2 over flag, cmd, size and payload
"""

from __future__ import annotations

from dataclasses import dataclass


class BfcdMspError(Exception):
    """A frame could not be encoded or decoded (bad header, CRC, or length)."""


# -- CRCs ---------------------------------------------------------------------


def _xor_crc(size: int, cmd: int, payload: bytes) -> int:
    crc = size ^ cmd
    for b in payload:
        crc ^= b
    return crc & 0xFF


def crc8_dvb_s2(crc: int, byte: int) -> int:
    """One step of the CRC8/DVB-S2 used by MSP v2 (polynomial 0xD5)."""
    crc ^= byte
    for _ in range(8):
        if crc & 0x80:
            crc = ((crc << 1) ^ 0xD5) & 0xFF
        else:
            crc = (crc << 1) & 0xFF
    return crc


def crc8_dvb_s2_buf(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = crc8_dvb_s2(crc, b)
    return crc


# -- frames -------------------------------------------------------------------


@dataclass
class MspFrame:
    """A decoded MSP frame."""

    version: int          # 1 or 2
    direction: str        # '<', '>' or '!'
    cmd: int
    payload: bytes
    ok: bool              # False for an error frame ('!')


def encode_request(cmd: int, payload: bytes = b"", *, version: int = 1,
                   flag: int = 0) -> bytes:
    """Encode an MSP request frame.

    v1 only carries 8-bit commands and payloads up to 255 bytes; a command id
    >= 256 or an oversized payload requires v2 (and is rejected for v1 here).
    """
    payload = bytes(payload)
    if version == 1:
        if cmd > 0xFF:
            raise BfcdMspError(f"command {cmd} needs MSP v2 (id > 255)")
        if len(payload) > 0xFF:
            raise BfcdMspError("payload too large for MSP v1 (> 255 bytes)")
        frame = bytearray(b"$M<")
        frame.append(len(payload))
        frame.append(cmd)
        frame += payload
        frame.append(_xor_crc(len(payload), cmd, payload))
        return bytes(frame)
    if version == 2:
        if cmd > 0xFFFF:
            raise BfcdMspError("command id too large for MSP v2 (> 65535)")
        if len(payload) > 0xFFFF:
            raise BfcdMspError("payload too large for MSP v2 (> 65535 bytes)")
        body = bytearray()
        body.append(flag & 0xFF)
        body += cmd.to_bytes(2, "little")
        body += len(payload).to_bytes(2, "little")
        body += payload
        frame = bytearray(b"$X<")
        frame += body
        frame.append(crc8_dvb_s2_buf(bytes(body)))
        return bytes(frame)
    raise BfcdMspError(f"unknown MSP version {version}")


def decode_frame(data: bytes) -> MspFrame:
    """Decode exactly one MSP frame from the start of ``data``.

    Raises :class:`BfcdMspError` if the buffer is not a single complete, valid
    frame. For streaming reads where the buffer may be partial, use
    :func:`try_decode` instead.
    """
    frame, consumed = try_decode(data)
    if frame is None:
        raise BfcdMspError("incomplete MSP frame")
    if consumed != len(data):
        raise BfcdMspError(f"trailing bytes after MSP frame ({len(data) - consumed} extra)")
    return frame


def try_decode(data: bytes) -> tuple[MspFrame | None, int]:
    """Try to decode one frame from the front of ``data``.

    Returns ``(frame, bytes_consumed)`` on success, or ``(None, 0)`` when more
    bytes are needed. Raises :class:`BfcdMspError` only on a *definitely* corrupt
    header or CRC (so a streaming caller can resynchronise). This is the parser
    the probe's read loop drives as bytes arrive.
    """
    if len(data) < 3:
        return None, 0
    if data[0:1] != b"$":
        raise BfcdMspError(f"bad MSP magic byte {data[0]:#04x}")
    proto = data[1:2]
    direction = chr(data[2])
    if direction not in "<>!":
        raise BfcdMspError(f"bad MSP direction {data[2]:#04x}")

    if proto == b"M":
        if len(data) < 5:
            return None, 0
        size = data[3]
        cmd = data[4]
        total = 5 + size + 1
        if len(data) < total:
            return None, 0
        payload = bytes(data[5:5 + size])
        crc = data[5 + size]
        if crc != _xor_crc(size, cmd, payload):
            raise BfcdMspError(f"MSP v1 CRC mismatch for cmd {cmd}")
        return MspFrame(1, direction, cmd, payload, direction != "!"), total
    if proto == b"X":
        if len(data) < 8:
            return None, 0
        flag = data[3]
        cmd = int.from_bytes(data[4:6], "little")
        size = int.from_bytes(data[6:8], "little")
        total = 8 + size + 1
        if len(data) < total:
            return None, 0
        payload = bytes(data[8:8 + size])
        crc = data[8 + size]
        body = bytes([flag]) + cmd.to_bytes(2, "little") + size.to_bytes(2, "little") + payload
        if crc != crc8_dvb_s2_buf(body):
            raise BfcdMspError(f"MSP v2 CRC mismatch for cmd {cmd}")
        return MspFrame(2, direction, cmd, payload, direction != "!"), total
    raise BfcdMspError(f"bad MSP protocol byte {data[1]:#04x}")
