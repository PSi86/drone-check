"""Flight-controller session: identify via MSP, capture via CLI.

Two implementations share one interface so the orchestrator is agnostic:

* :class:`RealFlightController` talks to hardware over a serial transport.
* :class:`FakeFlightController` replays a canned profile for offline demos and
  tests (no serial port, no MSP byte simulation needed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from .cli_session import CliSession, supports_framed_cli
from .msp import MspClient, MspIdentity
from .transport import LoggingTransport, SerialTransport, Transport


class FlightController(Protocol):
    def identify(self) -> MspIdentity: ...

    def run_cli(self, commands: list[str]) -> dict[str, str]:
        """Enter the CLI, run each command, leave cleanly. Returns cmd -> output."""
        ...

    def close(self) -> None: ...


class RealFlightController:
    """Hardware-backed session over a :class:`Transport`."""

    def __init__(self, transport: Transport, idle_timeout: float = 1.5, max_wait: float = 30.0):
        self._t = transport
        self._msp = MspClient(transport)
        self._cli = CliSession(transport, idle_timeout=idle_timeout, max_wait=max_wait)
        self._version = ""  # FC version string, cached by identify()

    @classmethod
    def open(
        cls,
        port: str,
        baudrate: int = 115200,
        connect_delay: float = 0.3,
        idle_timeout: float = 1.5,
        max_wait: float = 30.0,
        debug_path: Optional[Path] = None,
    ) -> "RealFlightController":
        transport: Transport = SerialTransport(
            port, baudrate=baudrate, connect_delay=connect_delay
        )
        if debug_path is not None:
            transport = LoggingTransport(transport, debug_path)
        return cls(transport, idle_timeout=idle_timeout, max_wait=max_wait)

    def identify(self) -> MspIdentity:
        ident = self._msp.identify()
        self._version = ident.version or ""
        return ident

    def _supports_framed_cli(self) -> bool:
        """Whether this FC uses the framed MSP-CLI (Betaflight >= 4.5.4 and the
        2025.x scheme) rather than the raw ``#`` prompt. See
        :func:`drone_check.cli_session.supports_framed_cli`."""
        return supports_framed_cli(self._version)

    def run_cli(self, commands: list[str]) -> dict[str, str]:
        if self._supports_framed_cli():
            # Framed CLI: each command is self-contained (STX..ETX), so there is
            # no CLI mode to enter or exit and nothing reboots.
            return {cmd: self._cli.command_framed(cmd) for cmd in commands}

        outputs: dict[str, str] = {}
        self._cli.enter()
        try:
            for cmd in commands:
                outputs[cmd] = self._cli.command(cmd)
        finally:
            # Leave without rebooting: a reboot would drop the USB port and look
            # like a disconnect. The port stays until the operator unplugs the
            # drone, which is exactly the "wait for serial disconnect" workflow.
            self._cli.exit_clean(reboot=False)
        return outputs

    def close(self) -> None:
        self._t.close()


@dataclass
class FcProfile:
    """A canned flight controller, used by :class:`FakeFlightController`."""

    identity: MspIdentity
    cli_outputs: dict[str, str] = field(default_factory=dict)


class FakeFlightController:
    """Replays an :class:`FcProfile` without any serial I/O."""

    def __init__(self, profile: FcProfile):
        self._profile = profile

    def identify(self) -> MspIdentity:
        return self._profile.identity

    def run_cli(self, commands: list[str]) -> dict[str, str]:
        return {cmd: self._profile.cli_outputs.get(cmd, "") for cmd in commands}

    def close(self) -> None:
        pass
