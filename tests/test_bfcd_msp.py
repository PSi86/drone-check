import pytest

from drone_check.bfcd.msp import (
    BfcdMspError,
    crc8_dvb_s2_buf,
    decode_frame,
    encode_request,
    try_decode,
)
from drone_check.bfcd.probe import MspProbe


def test_v1_api_version_request_bytes():
    # The exact frame the existing SITL probe sends: $M< len=0 cmd=1 crc=1.
    assert encode_request(1) == b"$M<\x00\x01\x01"


def test_v1_round_trip():
    frame_bytes = encode_request(2, b"BTFL", version=1)
    # Re-decode the same bytes as if they were a response.
    resp = frame_bytes[:2] + b">" + frame_bytes[3:]
    frame = decode_frame(resp)
    assert frame.version == 1
    assert frame.cmd == 2
    assert frame.payload == b"BTFL"
    assert frame.ok


def test_v2_round_trip_large_cmd():
    payload = bytes(range(20))
    req = encode_request(300, payload, version=2)
    resp = req[:2] + b">" + req[3:]
    frame = decode_frame(resp)
    assert frame.version == 2
    assert frame.cmd == 300
    assert frame.payload == payload
    assert frame.ok


def test_v1_command_too_large_needs_v2():
    with pytest.raises(BfcdMspError):
        encode_request(300, version=1)


def test_error_frame_is_not_ok():
    req = encode_request(1)
    err = req[:2] + b"!" + req[3:]
    frame = decode_frame(err)
    assert not frame.ok


def test_crc_mismatch_raises():
    req = encode_request(5, b"\x01\x02")
    bad = bytearray(req[:2] + b">" + req[3:])
    bad[-1] ^= 0xFF  # corrupt the CRC
    with pytest.raises(BfcdMspError):
        decode_frame(bytes(bad))


def test_try_decode_partial_needs_more():
    req = encode_request(5, b"\x01\x02\x03")
    resp = req[:2] + b">" + req[3:]
    frame, consumed = try_decode(resp[:4])
    assert frame is None and consumed == 0
    frame, consumed = try_decode(resp)
    assert frame is not None and consumed == len(resp)


def test_v2_crc_is_dvb_s2():
    # Body = flag(0) + cmd(1,0) + size(0,0); CRC over those four bytes.
    body = bytes([0, 1, 0, 0, 0])
    req = encode_request(1, version=2)
    assert req[-1] == crc8_dvb_s2_buf(body)


class LoopbackTransport:
    """A transport that answers each request via a responder callback.

    Lets the probe be exercised end-to-end without a real endpoint: the written
    request is decoded, handed to ``responder``, and its returned response bytes
    are queued for reading.
    """

    def __init__(self, responder):
        self._responder = responder
        self._rx = bytearray()

    def write(self, data: bytes) -> None:
        frame, _ = try_decode(data)
        if frame is not None:
            reply = self._responder(frame)
            if reply:
                self._rx += reply

    def read(self, size: int) -> bytes:
        chunk = bytes(self._rx[:size])
        del self._rx[: len(chunk)]
        return chunk

    def close(self) -> None:
        pass


def test_probe_query_round_trip():
    def responder(frame):
        # Echo an API-version-style payload back as a response frame.
        resp_payload = b"\x00\x01\x2e"  # api 1.46
        body = encode_request(frame.cmd, resp_payload, version=frame.version)
        return body[:2] + b">" + body[3:]

    probe = MspProbe(LoopbackTransport(responder), timeout=1.0)
    frame = probe.query(1)
    assert frame.ok
    assert frame.payload == b"\x00\x01\x2e"


def test_probe_run_collects_results():
    from drone_check.bfcd.commands import MspCommand, Priority, Handling

    cmds = [
        MspCommand("MSP_API_VERSION", 1, Priority.A, Handling.EXACT),
        MspCommand("MSP_FC_VARIANT", 2, Priority.A, Handling.EXACT),
    ]

    def responder(frame):
        body = encode_request(frame.cmd, b"OK", version=frame.version)
        return body[:2] + b">" + body[3:]

    probe = MspProbe(LoopbackTransport(responder), timeout=1.0)
    results = probe.run(cmds)
    assert [r.command for r in results] == ["MSP_API_VERSION", "MSP_FC_VARIANT"]
    assert all(r.ok and r.payload == b"OK" for r in results)
