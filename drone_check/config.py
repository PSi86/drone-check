"""Load configuration: settings, rules and the firmware allowlist."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Settings:
    log_dir: Path = Path("logs")
    serial_baudrate: int = 115200
    poll_interval: float = 1.0

    # Capture folder name template. Placeholders: {timestamp} {pilot_name}
    # {craft_name} {uid} {variant} {version}. Names come from the FC.
    folder_template: str = "{timestamp}_{pilot_name}_{craft_name}"

    # Manual pilot entry is OFF by default — the pilot name is read from the FC
    # and logs are never edited. When enabled, the operator may supply a
    # *fallback* pilot name used only for the folder label (never written into
    # the captured data files).
    allow_manual_pilot: bool = False

    # Serial / CLI timing (tunable for slow or finicky links).
    connect_delay: float = 0.3
    cli_idle_timeout: float = 1.5
    cli_max_wait: float = 30.0

    # USB hot-plug debounce. A port must be present continuously for
    # connect_debounce seconds before we read it, and absent continuously for
    # disconnect_debounce seconds before we consider the drone removed. This
    # absorbs cable wiggle and USB re-enumeration.
    connect_debounce: float = 3.0
    disconnect_debounce: float = 3.0

    # If a capture fails while the drone stays plugged in, retry this many extra
    # times before giving up (the drone must be unplugged + replugged to try
    # again). Prevents an endlessly-failing drone from looping forever.
    capture_max_retries: int = 2

    # When set, raw serial traffic is teed to <debug_dir>/<port>-<time>.log.
    debug_dir: Path | None = None

    # Session application log: how many recent entries the web UI keeps/shows.
    log_list_length: int = 100

    # Firmware-hash verification. The acceptance level maps the (config-
    # independent) verification facts to the approved/not-approved verdict:
    #   "whitelist" – only an exact allowlist (official-release) hash
    #   "official"  – allowlist hash OR any commit that exists in the official
    #                 repo (default; "all official builds")
    #   "open"      – never reject an unknown hash
    hash_acceptance_level: str = "official"
    # Network toggle for the GitHub existence check (off = fully offline).
    hash_use_github: bool = True

    # CLI commands captured (in order) during the CLI phase. `dump all` is the
    # authoritative source (every setting with its absolute value). `diff all`
    # is NOT captured by default — add it here if you also want the portable,
    # human-readable backup stored alongside the dump.
    cli_commands: list[str] = field(default_factory=lambda: ["version", "dump all", "status"])

    # Only `dump all`/`dump` is parsed by default. Enable this to also let the
    # parser fall back to `diff all`/`diff` when no dump is present.
    parse_diff_fallback: bool = False

    # "View in Configurator": which backend serves a capture to the real web
    # Configurator. A single choice — the logs page shows one button for it.
    #   "bfcd" (default) – the lighter, read-only bf-configd backend
    #   "sitl"           – the full Betaflight SITL instance
    # The chosen backend must also be enabled below and its environment present.
    viewer_backend: str = "bfcd"

    # Load a capture into a version-matched Betaflight SITL (built under WSL by
    # scripts/build_sitl.sh) so the real web Configurator can connect to it.
    sitl_enabled: bool = True
    sitl_distro: str = "Ubuntu"  # WSL distro that has the SITL cache
    # WSL-side paths (~ expands inside the distro).
    sitl_cache_dir: str = "~/.cache/drone-check/sitl"
    sitl_run_dir: str = "~/.cache/drone-check/run"
    sitl_tcp_port: int = 5761  # SITL UART1
    sitl_ws_port: int = 6761  # websockify endpoint for the web Configurator
    sitl_boot_timeout: float = 30.0

    # bf-configd: a lighter, read-only alternative to SITL that serves a dump's
    # config to the Configurator over MSP without starting the flight loop. The
    # native backend is built per firmware family (see scripts/build_bfcd.sh).
    # Distinct ports from SITL so both can coexist. See drone_check/bfcd/.
    # Experimental read-only alternative to SITL; gated additionally by the Linux
    # environment being present (so it auto-hides where it can't run).
    bfcd_enabled: bool = True
    bfcd_cache_dir: str = "~/.cache/drone-check/bfcd"
    bfcd_run_dir: str = "~/.cache/drone-check/bfcd-run"
    # The bf-configd binary is derived from SITL and inherits SITL's hard-coded
    # UART->TCP mapping (UART1 == 5761), so it shares SITL's TCP base port; only
    # one of SITL / bf-configd can serve at a time. The websockify endpoint is
    # ours, so it gets a distinct port.
    bfcd_tcp_port: int = 5761
    bfcd_ws_port: int = 6762
    bfcd_boot_timeout: float = 30.0
    # Extra delay (seconds) after the MSP locate-probe before reporting ready, a
    # margin so SITL's single connection slot is certainly free for the
    # Configurator's first connect. Set to 0 to disable (the websockify start
    # already covers the slot's free time on a typical host).
    bfcd_ready_settle: float = 2.0
    # When the Configurator leaves CLI mode (or sends save/reboot), the backend
    # process exits like a rebooting FC. With this on, the session relaunches it
    # from the saved config so the Configurator can reconnect — mirroring a real
    # FC reboot. Rate-limited to avoid a crash loop.
    bfcd_auto_restart: bool = True


@dataclass
class AppConfig:
    settings: Settings
    rules: list[dict[str, Any]]
    allowlist: dict[str, Any]


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_settings(path: Path) -> Settings:
    data = _load_yaml(path) or {}
    s = Settings()
    if "log_dir" in data:
        s.log_dir = Path(data["log_dir"])
    s.serial_baudrate = int(data.get("serial_baudrate", s.serial_baudrate))
    s.poll_interval = float(data.get("poll_interval", s.poll_interval))
    s.folder_template = str(data.get("folder_template", s.folder_template))
    s.allow_manual_pilot = bool(data.get("allow_manual_pilot", s.allow_manual_pilot))
    s.connect_delay = float(data.get("connect_delay", s.connect_delay))
    s.cli_idle_timeout = float(data.get("cli_idle_timeout", s.cli_idle_timeout))
    s.cli_max_wait = float(data.get("cli_max_wait", s.cli_max_wait))
    s.connect_debounce = float(data.get("connect_debounce", s.connect_debounce))
    s.disconnect_debounce = float(data.get("disconnect_debounce", s.disconnect_debounce))
    s.capture_max_retries = int(data.get("capture_max_retries", s.capture_max_retries))
    if data.get("debug_dir"):
        s.debug_dir = Path(data["debug_dir"])
    s.log_list_length = int(data.get("log_list_length", s.log_list_length))

    hashcfg = data.get("firmware_hash", {}) or {}
    level = str(hashcfg.get("acceptance_level", s.hash_acceptance_level)).lower()
    if level not in ("whitelist", "official", "open"):
        level = s.hash_acceptance_level
    s.hash_acceptance_level = level
    s.hash_use_github = bool(hashcfg.get("use_github", s.hash_use_github))

    if "cli_commands" in data and data["cli_commands"]:
        s.cli_commands = [str(c) for c in data["cli_commands"]]
    s.parse_diff_fallback = bool(data.get("parse_diff_fallback", s.parse_diff_fallback))

    backend = str(data.get("viewer_backend", s.viewer_backend)).lower()
    s.viewer_backend = backend if backend in ("bfcd", "sitl") else s.viewer_backend

    sitl = data.get("sitl", {}) or {}
    s.sitl_enabled = bool(sitl.get("enabled", s.sitl_enabled))
    s.sitl_distro = str(sitl.get("distro", s.sitl_distro))
    s.sitl_cache_dir = str(sitl.get("cache_dir", s.sitl_cache_dir))
    s.sitl_run_dir = str(sitl.get("run_dir", s.sitl_run_dir))
    s.sitl_tcp_port = int(sitl.get("tcp_port", s.sitl_tcp_port))
    s.sitl_ws_port = int(sitl.get("ws_port", s.sitl_ws_port))
    s.sitl_boot_timeout = float(sitl.get("boot_timeout", s.sitl_boot_timeout))

    bfcd = data.get("bfcd", {}) or {}
    s.bfcd_enabled = bool(bfcd.get("enabled", s.bfcd_enabled))
    s.bfcd_cache_dir = str(bfcd.get("cache_dir", s.bfcd_cache_dir))
    s.bfcd_run_dir = str(bfcd.get("run_dir", s.bfcd_run_dir))
    s.bfcd_tcp_port = int(bfcd.get("tcp_port", s.bfcd_tcp_port))
    s.bfcd_ws_port = int(bfcd.get("ws_port", s.bfcd_ws_port))
    s.bfcd_boot_timeout = float(bfcd.get("boot_timeout", s.bfcd_boot_timeout))
    s.bfcd_ready_settle = float(bfcd.get("ready_settle", s.bfcd_ready_settle))
    s.bfcd_auto_restart = bool(bfcd.get("auto_restart", s.bfcd_auto_restart))
    return s


def load_config(config_dir: Path) -> AppConfig:
    settings = load_settings(config_dir / "settings.yaml")
    rules_doc = _load_yaml(config_dir / "rules.yaml") or {}
    rules = rules_doc.get("rules", []) if isinstance(rules_doc, dict) else (rules_doc or [])
    allowlist = _load_yaml(config_dir / "firmware_allowlist.yaml") or {}
    return AppConfig(settings=settings, rules=rules, allowlist=allowlist)
