"""Command-line entry point.

    drone-check serve              # start the local web UI + hot-plug watcher
    drone-check ports              # list available serial ports
    drone-check probe <port>       # low-level bring-up: identify + raw CLI dump
    drone-check inspect <port>     # full capture + evaluation, print report
    drone-check demo               # run the pipeline against built-in sample drones

Hardware-bring-up flags (probe / inspect):
    --baud N            serial baud rate (default from settings.yaml)
    --connect-delay S   settle time after opening the port
    --debug             tee raw serial traffic to ./debug/<port>-<time>.log
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Settings, load_config
from .flightcontroller import FakeFlightController, RealFlightController
from .orchestrator import Orchestrator
from .storage import render_report


def _default_config_dir() -> Path:
    cwd = Path.cwd() / "config"
    if cwd.exists():
        return cwd
    return Path(__file__).resolve().parent.parent / "config"


def _debug_path(settings: Settings, port: str, enabled: bool) -> Optional[Path]:
    if not enabled:
        return None
    base = settings.debug_dir or Path("debug")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_port = port.replace("/", "_").replace("\\", "_").replace(":", "")
    return base / f"{safe_port}-{stamp}.log"


def _open_fc(args: argparse.Namespace, settings: Settings) -> RealFlightController:
    baud = getattr(args, "baud", None) or settings.serial_baudrate
    connect_delay = getattr(args, "connect_delay", None)
    if connect_delay is None:
        connect_delay = settings.connect_delay
    debug = _debug_path(settings, args.port, getattr(args, "debug", False))
    if debug is not None:
        print(f"Debug log: {debug}", file=sys.stderr)
    return RealFlightController.open(
        args.port,
        baudrate=baud,
        connect_delay=connect_delay,
        idle_timeout=settings.cli_idle_timeout,
        max_wait=settings.cli_max_wait,
        debug_path=debug,
    )


def cmd_ports(args: argparse.Namespace) -> int:
    from . import serialwatch

    ports = serialwatch.list_ports()
    if not ports:
        print("No serial ports found.")
        return 0
    for p in ports:
        vid = f"{p.vid:04x}" if p.vid is not None else "----"
        pid = f"{p.pid:04x}" if p.pid is not None else "----"
        print(f"{p.device:12}  VID:PID={vid}:{pid}  {p.description}")
    return 0


def _install_stop_on_enter(server, on_stop=None):
    """Stop the server cleanly when the operator presses Enter.

    Windows PowerShell 5.1 does not reliably deliver Ctrl+C to a child program,
    so Enter (typed stdin always reaches the foreground program) is a dependable
    alternative. This triggers the SAME graceful shutdown as Ctrl+C — it sets
    uvicorn's should_exit, so the app lifespan shutdown runs (no process kill).
    Only enabled on an interactive TTY; DRONE_CHECK_STOP_ON_STDIN=1 forces it on
    for tests. Returns the reader thread (or None if not enabled).
    """
    import os
    import threading

    stream = sys.stdin
    forced = os.environ.get("DRONE_CHECK_STOP_ON_STDIN") == "1"
    if stream is None or (not forced and not stream.isatty()):
        return None

    def _stop() -> None:
        print("\nStopping drone-check...", file=sys.stderr, flush=True)
        server.should_exit = True  # graceful: uvicorn runs the lifespan shutdown

    action = on_stop or _stop

    def _reader() -> None:
        try:
            stream.readline()  # blocks until Enter (or EOF on a closed stdin)
        except Exception:
            pass
        action()

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return thread


def _install_windows_ctrl_handler(server):
    """Make Ctrl+C (and window close) trigger a graceful shutdown on Windows.

    Python's SIGINT delivery is unreliable under the asyncio Proactor loop on
    Windows: a pending Ctrl+C is not processed while the loop is blocked, so
    Ctrl+C appears to do nothing until other console I/O (e.g. pressing Enter)
    wakes the loop — which is exactly the "press Enter, then Ctrl+C" symptom. A
    native console control handler runs in its own OS thread the moment Ctrl+C is
    pressed, independent of the loop, and just flips ``should_exit``; uvicorn's
    main loop polls that every 100 ms and runs the lifespan shutdown.

    Returns the handler (keep a reference so ctypes does not garbage-collect it),
    or None off Windows.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    handled = {0, 1, 2, 5, 6}  # CTRL_C, CTRL_BREAK, CTRL_CLOSE, LOGOFF, SHUTDOWN
    state = {"asked": False}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def _handler(ctrl_type):
        if ctrl_type not in handled:
            return False
        if state["asked"]:
            # A second Ctrl+C: stop handling so the OS default force-kills, in
            # case a graceful shutdown is wedged.
            return False
        state["asked"] = True
        server.should_exit = True
        print("\nStopping drone-check...", file=sys.stderr, flush=True)
        return True  # handled — suppress the default (which would hard-kill)

    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True):
        return None
    return _handler


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    config = load_config(args.config)
    app = create_app(config, demo=args.demo)
    # Several clean ways to stop, all triggering uvicorn's graceful shutdown (which
    # runs the app lifespan shutdown — ending any SITL session and terminating the
    # WSL distro we started): Ctrl+C, pressing Enter here, or the "Server beenden"
    # button in the web UI. timeout_graceful_shutdown caps the wait on open
    # connections so the clean stop can't hang.
    #
    # On Windows we own Ctrl+C via a native console control handler (see
    # _install_windows_ctrl_handler) and disable uvicorn's own signal handlers,
    # because Python-level SIGINT is unreliable under the Proactor loop there
    # (Ctrl+C only registered after pressing Enter). Other platforms keep
    # uvicorn's default signal handling.
    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            if sys.platform == "win32":
                return
            super().install_signal_handlers()

    server = _Server(uvicorn.Config(
        app, host=args.host, port=args.port, log_level="info",
        timeout_graceful_shutdown=10,
    ))
    app.state.uvicorn_server = server  # lets POST /api/shutdown stop it cleanly
    _install_stop_on_enter(server)
    _ctrl_handler = _install_windows_ctrl_handler(server)  # noqa: F841 (kept alive)
    server.run()
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Bring-up helper: identify over MSP and dump raw CLI output, no rules."""
    config = load_config(args.config)
    fc = _open_fc(args, config.settings)
    try:
        print("== MSP identify ==", file=sys.stderr)
        ident = fc.identify()
        for field_name in (
            "variant", "version", "api_version", "board_name",
            "build_date", "build_time", "git_hash", "uid",
        ):
            print(f"  {field_name:12}: {getattr(ident, field_name)}")

        print("== CLI capture ==", file=sys.stderr)
        outputs = fc.run_cli(config.settings.cli_commands)
    finally:
        fc.close()

    for cmd, text in outputs.items():
        n = len(text.splitlines())
        print(f"\n--- {cmd} ({n} lines) ---")
        if args.raw:
            print(text)
        else:
            print("\n".join(text.splitlines()[:8]))
            if n > 8:
                print("  ... (use --raw for full output)")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    def emit(evt: dict) -> None:
        if evt.get("type") == "step":
            print(f"  [{evt.get('status','')}] {evt.get('step')}", file=sys.stderr)
        elif evt.get("type") == "error":
            print(f"  ERROR: {evt.get('message')}", file=sys.stderr)

    orch = Orchestrator(config, emit=emit)
    fc = _open_fc(args, config.settings)
    try:
        # The pilot/craft names come from the FC; --pilot is only a folder-label
        # fallback used when the FC reports no pilot name.
        snapshot, evaluation, out = orch.process(fc, pilot_fallback=args.pilot or "")
    finally:
        fc.close()
    print(render_report(snapshot, evaluation))
    print(f"Saved to: {out}")
    return 0 if evaluation.passed else 2


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import demo_profiles, seed_allowlist

    config = copy.deepcopy(load_config(args.config))
    seed_allowlist(config.allowlist)

    orch = Orchestrator(config, emit=lambda e: None)
    any_fail = False
    for profile in demo_profiles():
        snapshot, evaluation, out = orch.process(FakeFlightController(profile))
        print(render_report(snapshot, evaluation))
        print(f"Saved to: {out}\n")
        any_fail = any_fail or not evaluation.passed
    return 1 if any_fail else 0


def _add_serial_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("port", help="serial port, e.g. COM5 or /dev/ttyACM0")
    p.add_argument("--baud", type=int, default=None, help="serial baud rate")
    p.add_argument("--connect-delay", type=float, default=None, help="settle time after open (s)")
    p.add_argument("--debug", action="store_true", help="tee raw serial traffic to ./debug/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drone-check")
    parser.add_argument(
        "--config", type=Path, default=_default_config_dir(), help="config directory"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ports = sub.add_parser("ports", help="list available serial ports")
    p_ports.set_defaults(func=cmd_ports)

    p_serve = sub.add_parser("serve", help="run the local web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--demo", action="store_true", help="no serial watcher; use /api/demo")
    p_serve.set_defaults(func=cmd_serve)

    p_probe = sub.add_parser("probe", help="low-level identify + raw CLI dump (no rules)")
    _add_serial_args(p_probe)
    p_probe.add_argument("--raw", action="store_true", help="print full CLI output")
    p_probe.set_defaults(func=cmd_probe)

    p_inspect = sub.add_parser("inspect", help="full capture + evaluation")
    _add_serial_args(p_inspect)
    p_inspect.add_argument(
        "--pilot", default="", help="fallback pilot name for the folder label (FC value wins)"
    )
    p_inspect.set_defaults(func=cmd_inspect)

    p_demo = sub.add_parser("demo", help="run against built-in sample drones")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
