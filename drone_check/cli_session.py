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

# Framed CLI-over-serial ("MSP CLI passthrough"), used by Betaflight >= 4.5.4 and
# the modern Configurator instead of the raw `#` prompt. Each command is sent as
# STX <command> LF ETX; the reply is STX <output, LF-separated lines> ETX. This
# coexists with MSP ($M<) on the same link and needs no prompt detection.
STX = b"\x02"
ETX = b"\x03"
LF = b"\x0a"


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
        """Enter CLI mode and return the banner text.

        Used for the legacy raw-``#`` CLI (Betaflight < 4.5.4 / INAV). Newer
        Betaflight uses the framed CLI (see command_framed), so it does not call
        this.
        """
        # Discard any leftover MSP bytes from the identification phase first.
        drain(self._t, 0.2)
        self._t.write(CLI_ENTER + b"\r")
        banner = read_until(self._t, PROMPT, self._idle, self._max_wait)
        # The stream must END with the prompt — not merely contain it (dump/diff
        # output is full of "# ..." comment lines). Anything else means the read
        # timed out, i.e. the link stalled before the CLI was ready.
        if not banner.endswith(PROMPT):
            raise CliError("did not receive CLI prompt after entering CLI mode")
        self._in_cli = True
        return banner.decode("ascii", "replace")

    def command(self, cmd: str) -> str:
        """Run one CLI command and return its output (prompt/echo stripped).

        Completeness is enforced by requiring the output to **end** with the
        prompt. A substring check is not enough: dump/diff output contains many
        "# ..." comment lines, so a read truncated by a timeout would still
        "contain" a prompt. Ending with the prompt means the FC finished writing
        and is waiting for input — i.e. the response is complete.
        """
        if not self._in_cli:
            raise CliError("CLI session not entered")
        self._t.write(cmd.encode("ascii") + b"\r")
        raw = read_until(self._t, PROMPT, self._idle, self._max_wait)
        if not raw.endswith(PROMPT):
            raise CliError(
                f"incomplete response for command {cmd!r}: "
                "stream did not end at the CLI prompt (link stalled / truncated)"
            )
        text = raw.decode("ascii", "replace")
        return _strip_echo_and_prompt(text, cmd)

    def command_framed(self, cmd: str) -> str:
        """Run one CLI command via the framed MSP-CLI protocol (Betaflight
        >= 4.5.4): send ``STX <cmd> LF ETX`` and read the reply up to ``ETX``.

        No CLI 'mode' is entered and the FC does not echo the command, so unlike
        :meth:`command` there is no prompt to match or echo to strip — the ETX
        delimits a complete response, which is far more robust on newer firmware
        that ignores the raw ``#`` CLI-enter byte.
        """
        self._t.write(STX + cmd.encode("ascii", "replace") + LF + ETX)
        raw = read_until(self._t, ETX, self._idle, self._max_wait)
        if not raw.endswith(ETX):
            raise CliError(
                f"incomplete framed CLI response for command {cmd!r}: "
                "stream did not end at ETX (link stalled / truncated)"
            )
        return _decode_framed(raw)

    def exit_clean(self, reboot: bool = True) -> None:
        """Leave the CLI. ``exit`` reboots the FC into normal mode (no save)."""
        if not self._in_cli:
            return
        try:
            self._t.write(b"exit\r" if reboot else b"exit noreboot\r")
            time.sleep(0.2)
        finally:
            self._in_cli = False


def _decode_framed(raw: bytes) -> str:
    """Decode a framed CLI reply (``STX <body> ETX``) to plain text.

    Strips the leading STX and trailing ETX, drops CRs, and keeps LF-separated
    lines (the FC frames the whole command output in one STX..ETX block)."""
    body = raw
    start = body.find(STX)
    if start != -1:
        body = body[start + 1:]
    if body.endswith(ETX):
        body = body[:-1]
    return body.replace(b"\r", b"").decode("ascii", "replace")


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
