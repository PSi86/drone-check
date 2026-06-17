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


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    config = load_config(args.config)
    app = create_app(config, demo=args.demo)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
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

    def ask_pilot(ctx: dict) -> str:
        if not config.settings.ask_pilot_name:
            return ""
        try:
            return input("Pilot name (optional): ").strip()
        except EOFError:
            return ""

    def emit(evt: dict) -> None:
        if evt.get("type") == "step":
            print(f"  [{evt.get('status','')}] {evt.get('step')}", file=sys.stderr)
        elif evt.get("type") == "error":
            print(f"  ERROR: {evt.get('message')}", file=sys.stderr)

    orch = Orchestrator(config, emit=emit, ask_pilot=ask_pilot)
    fc = _open_fc(args, config.settings)
    try:
        snapshot, evaluation, out = orch.process(fc)
    finally:
        fc.close()
    print(render_report(snapshot, evaluation))
    print(f"Saved to: {out}")
    return 0 if evaluation.passed else 2


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import demo_profiles, seed_allowlist

    config = copy.deepcopy(load_config(args.config))
    seed_allowlist(config.allowlist)

    orch = Orchestrator(config, emit=lambda e: None, ask_pilot=lambda ctx: "Demo Pilot")
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
    p_inspect.set_defaults(func=cmd_inspect)

    p_demo = sub.add_parser("demo", help="run against built-in sample drones")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
