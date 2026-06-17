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

    # When set, raw serial traffic is teed to <debug_dir>/<port>-<time>.log.
    debug_dir: Path | None = None

    # Session application log: how many recent entries the web UI keeps/shows.
    log_list_length: int = 100

    # Firmware-hash verification.
    hash_use_allowlist: bool = True
    hash_use_github: bool = True

    # CLI commands captured (in order) during the CLI phase. `dump all` is the
    # authoritative source (every setting with its absolute value). `diff all`
    # is NOT captured by default — add it here if you also want the portable,
    # human-readable backup stored alongside the dump.
    cli_commands: list[str] = field(default_factory=lambda: ["version", "dump all", "status"])

    # Only `dump all`/`dump` is parsed by default. Enable this to also let the
    # parser fall back to `diff all`/`diff` when no dump is present.
    parse_diff_fallback: bool = False


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
    if data.get("debug_dir"):
        s.debug_dir = Path(data["debug_dir"])
    s.log_list_length = int(data.get("log_list_length", s.log_list_length))

    hashcfg = data.get("firmware_hash", {}) or {}
    s.hash_use_allowlist = bool(hashcfg.get("use_allowlist", s.hash_use_allowlist))
    s.hash_use_github = bool(hashcfg.get("use_github", s.hash_use_github))

    if "cli_commands" in data and data["cli_commands"]:
        s.cli_commands = [str(c) for c in data["cli_commands"]]
    s.parse_diff_fallback = bool(data.get("parse_diff_fallback", s.parse_diff_fallback))
    return s


def load_config(config_dir: Path) -> AppConfig:
    settings = load_settings(config_dir / "settings.yaml")
    rules_doc = _load_yaml(config_dir / "rules.yaml") or {}
    rules = rules_doc.get("rules", []) if isinstance(rules_doc, dict) else (rules_doc or [])
    allowlist = _load_yaml(config_dir / "firmware_allowlist.yaml") or {}
    return AppConfig(settings=settings, rules=rules, allowlist=allowlist)
