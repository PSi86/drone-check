"""Dump-completeness guarantees: end-at-prompt + batch integrity."""

from pathlib import Path

import pytest

from drone_check.cli_session import CliError, CliSession
from drone_check.config import load_config
from drone_check.orchestrator import Orchestrator

pytest.importorskip("celpy")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class ScriptedTransport:
    """Serves a fixed byte stream; reads return b'' once exhausted."""

    def __init__(self, data: bytes):
        self._buf = bytearray(data)

    def write(self, data: bytes) -> None:
        pass

    def read(self, size: int) -> bytes:
        chunk = bytes(self._buf[:size])
        del self._buf[: len(chunk)]
        return chunk

    def close(self) -> None:
        pass


def _session(data: bytes) -> CliSession:
    cli = CliSession(ScriptedTransport(data), idle_timeout=0.2, max_wait=1.0)
    cli._in_cli = True  # skip enter() for a focused unit test
    return cli


def test_command_complete_when_stream_ends_at_prompt():
    data = b"dump all\r\nset a = 1\r\n# master\r\nset b = 2\r\nbatch end\r\n# "
    out = _session(data).command("dump all")
    assert "batch end" in out and "set b = 2" in out


def test_command_truncated_midstream_is_rejected():
    # Contains "# master" (a comment) but does NOT end at the prompt -> truncated.
    data = b"dump all\r\nset a = 1\r\n# master\r\nset b = 2\r\n"
    with pytest.raises(CliError):
        _session(data).command("dump all")


def _orch() -> Orchestrator:
    return Orchestrator(load_config(CONFIG_DIR))


def test_validate_accepts_betaflight_dump_ending_in_save():
    orch = _orch()
    # Betaflight `dump all` ends with "# save configuration" then "save".
    orch._validate_cli(
        {"dump all": "batch start\nset a = 1\nset b = 2\nset c = 3\n# save configuration\nsave\n"}
    )


def test_validate_accepts_diff_ending_in_batch_end():
    orch = _orch()
    orch._validate_cli(
        {"dump all": "batch start\nset a = 1\nset b = 2\nset c = 3\nbatch end\n"}
    )


def test_validate_rejects_dump_truncated_before_terminator():
    orch = _orch()
    # Plenty of settings but the stream stopped mid-dump (no save / batch end).
    body = "batch start\n" + "".join(f"set v{i} = {i}\n" for i in range(20))
    with pytest.raises(ValueError):
        orch._validate_cli({"dump all": body})


def test_validate_rejects_empty_dump():
    orch = _orch()
    with pytest.raises(ValueError):
        orch._validate_cli({"dump all": "# Betaflight\nset only = 1\nsave\n"})  # <3 set lines
