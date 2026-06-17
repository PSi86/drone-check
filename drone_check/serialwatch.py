"""USB hot-plug detection by polling the serial-port list.

``pyserial``'s ``list_ports`` works on Windows, Linux and macOS, so a simple
poll loop is the portable way to notice a flight controller being plugged in or
unplugged. We filter to likely flight-controller USB CDC devices by VID where
possible, but fall back to any new serial port.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

# USB vendor IDs commonly seen on STM32-based flight controllers (VCP / DFU).
# 0x0483 = STMicroelectronics. Others are kept open via the fallback.
_LIKELY_VIDS = {0x0483}


@dataclass
class PortInfo:
    device: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    description: str = ""


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as _lp

    ports = []
    for p in _lp.comports():
        ports.append(
            PortInfo(
                device=p.device,
                vid=getattr(p, "vid", None),
                pid=getattr(p, "pid", None),
                description=getattr(p, "description", "") or "",
            )
        )
    return ports


def _is_candidate(port: PortInfo) -> bool:
    if port.vid in _LIKELY_VIDS:
        return True
    # Fall back to any serial device; flight controllers from various vendors
    # enumerate under different VIDs, and CP210x/CH340-based ones differ again.
    return True


def watch(
    on_connect: Callable[[PortInfo], None],
    on_disconnect: Callable[[str], None],
    poll_interval: float = 1.0,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Block, polling for serial-port changes until ``should_stop`` returns True.

    ``on_connect`` fires once per newly appeared candidate port; ``on_disconnect``
    fires with the device name when a known port goes away.
    """
    known: set[str] = {p.device for p in list_ports()}
    while not should_stop():
        current = {p.device: p for p in list_ports()}
        current_names = set(current)

        for name in current_names - known:
            port = current[name]
            if _is_candidate(port):
                on_connect(port)

        for name in known - current_names:
            on_disconnect(name)

        known = current_names
        time.sleep(poll_interval)


def wait_for_disconnect(
    device: str,
    poll_interval: float = 0.5,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Block until ``device`` disappears from the serial-port list."""
    while not should_stop():
        names = {p.device for p in list_ports()}
        if device not in names:
            return
        time.sleep(poll_interval)
