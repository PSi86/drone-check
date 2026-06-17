"""End-to-end test through the real MSP + CLI code over a byte-level FC sim."""

from fakeserial import FakeSerialFC

from drone_check.capture import build_snapshot
from drone_check.flightcontroller import RealFlightController
from drone_check.transport import read_until


def _fc() -> RealFlightController:
    return RealFlightController(FakeSerialFC(), idle_timeout=0.4, max_wait=5.0)


def test_msp_identify_over_wire():
    ident = _fc().identify()
    assert ident.variant == "BTFL"
    assert ident.version == "4.5.1"
    assert ident.api_version == "1.46"
    assert ident.board_name == "HBFCS405"
    assert ident.git_hash == "77d01ba"  # 7-char MSP build hash
    assert len(ident.uid) == 24  # 3x uint32 as hex


def test_cli_capture_not_truncated_by_comment_lines():
    fc = _fc()
    fc.identify()
    outputs = fc.run_cli(["version", "dump all", "status"])
    # The version echo line must be stripped, leaving the firmware header.
    assert outputs["version"].startswith("# Betaflight")
    # dump all is full of "# ..." comment lines; capture must reach the very end
    # ("batch end") rather than stopping at the first "# " comment.
    assert "vtx_low_power_disarm = ON" in outputs["dump all"]
    assert "batch end" in outputs["dump all"]
    assert "MCU F405" in outputs["status"]


def test_full_snapshot_from_simulated_fc():
    fc = _fc()
    ident = fc.identify()
    outputs = fc.run_cli(["version", "dump all", "status"])
    snap = build_snapshot(ident, outputs, captured_at="t0")
    assert snap.firmware.variant == "BTFL"
    assert snap.firmware.board_name == "HBFCS405"
    # CLI version line (9-char hash) wins over the MSP 7-char hash.
    assert snap.firmware.git_hash == "77d01ba3b"
    assert snap.vtx.power_armed_max_mw == 200


def test_read_until_stops_only_at_trailing_prompt():
    # A transport whose stream contains "# " mid-data then ends with the prompt.
    class S:
        def __init__(self):
            self.data = bytearray(b"# comment line\r\nmore data\r\n# ")

        def read(self, n):
            chunk = bytes(self.data[:n])
            del self.data[: len(chunk)]
            return chunk

        def write(self, d):
            pass

        def close(self):
            pass

    got = read_until(S(), b"# ", idle_timeout=0.3, max_wait=2.0, settle=0.05)
    assert got.endswith(b"# ")
    assert b"more data" in got  # did not stop at the first "# "
