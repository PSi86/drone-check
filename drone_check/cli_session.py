"""Drive the Betaflight / INAV text CLI over the serial transport.

Flow: send ``#`` to enter the CLI, run a sequence of commands (reading each
response up to the ``# `` prompt), check the output for completeness, then leave
cleanly with ``exit`` so the flight controller reboots back to normal operation.
"""

from __future__ import annotations

import time

from .transport import Transport, drain, read_until

PROMPT = b"# "
CLI_ENTER = b"#"


class CliError(Exception):
    """Raised when the CLI does not behave as expected (timeout, truncation)."""


class CliSession:
    def __init__(
        self,
        transport: Transport,
        idle_timeout: float = 1.5,
        max_wait: float = 30.0,
    ):
        self._t = transport
        self._idle = idle_timeout
        self._max_wait = max_wait
        self._in_cli = False

    def enter(self) -> str:
        """Enter CLI mode and return the banner text."""
        # Discard any leftover MSP bytes from the identification phase first.
        drain(self._t, 0.2)
        self._t.write(CLI_ENTER + b"\r")
        banner = read_until(self._t, PROMPT, self._idle, self._max_wait)
        if PROMPT not in banner:
            raise CliError("did not receive CLI prompt after entering CLI mode")
        self._in_cli = True
        return banner.decode("ascii", "replace")

    def command(self, cmd: str) -> str:
        """Run one CLI command and return its output (prompt/echo stripped).

        Completeness is enforced by requiring the prompt to reappear; a missing
        prompt means the output was truncated or the link stalled.
        """
        if not self._in_cli:
            raise CliError("CLI session not entered")
        self._t.write(cmd.encode("ascii") + b"\r")
        raw = read_until(self._t, PROMPT, self._idle, self._max_wait)
        if PROMPT not in raw:
            raise CliError(f"incomplete response for command {cmd!r} (no prompt)")
        text = raw.decode("ascii", "replace")
        return _strip_echo_and_prompt(text, cmd)

    def exit_clean(self, reboot: bool = True) -> None:
        """Leave the CLI. ``exit`` reboots the FC into normal mode (no save)."""
        if not self._in_cli:
            return
        try:
            self._t.write(b"exit\r" if reboot else b"exit noreboot\r")
            time.sleep(0.2)
        finally:
            self._in_cli = False


def _strip_echo_and_prompt(text: str, cmd: str) -> str:
    """Remove the echoed command line and the trailing prompt from output."""
    # Normalise CR/LF so callers get clean lines regardless of FC line endings.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Drop a leading echo of the command itself.
    if lines and lines[0].strip() == cmd.strip():
        lines = lines[1:]
    # Drop trailing prompt-only / empty lines.
    while lines and lines[-1].strip() in ("#", ""):
        lines.pop()
    return "\n".join(lines)
