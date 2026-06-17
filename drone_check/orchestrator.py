"""Per-drone workflow: identify -> capture -> verify -> evaluate -> store.

The orchestrator is transport-agnostic: it takes a :class:`FlightController`
(real or fake) and drives the full sequence, emitting structured events so any
front-end (web UI, CLI) can render live progress. Each step validates the data
it receives for completeness before moving on.

The pilot name is deliberately *not* part of this flow: capturing the drone must
not wait on operator input. The pilot and craft names are read from the flight
controller itself and the capture is written once, immutably (see
:func:`storage.save_capture`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .capture import build_snapshot
from .config import AppConfig
from .firmware import FirmwareVerifier, verify_snapshot
from .flightcontroller import FlightController
from .model import DroneSnapshot, Evaluation
from .rules import RuleEngine, load_rules
from .storage import save_capture

EmitFn = Callable[[dict], None]

# A complete dump/diff ends with one of these (from the BF/INAV cli.c source):
#   Betaflight `dump`/`dump all`  -> "save"
#   Betaflight `diff` / all INAV  -> "batch end"
_DUMP_TERMINATORS = ("save", "batch end")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


class Orchestrator:
    def __init__(self, config: AppConfig, emit: Optional[EmitFn] = None):
        self.config = config
        self._emit = emit or (lambda evt: None)

        self._verifier = FirmwareVerifier(
            allowlist=config.allowlist,
            use_allowlist=config.settings.hash_use_allowlist,
            use_github=config.settings.hash_use_github,
        )
        # Build the rule engine eagerly so a bad rules.yaml fails fast.
        self._engine = RuleEngine(load_rules(config.rules))

    def process(
        self, fc: FlightController, pilot_fallback: str = ""
    ) -> tuple[DroneSnapshot, Evaluation, Path]:
        """Run the full workflow for one connected flight controller.

        ``pilot_fallback`` is used only for the folder label when the FC reports
        no pilot name; it never enters the captured data files.
        """
        captured_at = _now_iso()

        # 1. Identify via MSP (machine-readable, validated for completeness).
        self._emit({"type": "step", "step": "identify", "status": "running"})
        ident = fc.identify()
        self._emit(
            {
                "type": "step",
                "step": "identify",
                "status": "ok",
                "info": {
                    "variant": ident.variant,
                    "version": ident.version,
                    "uid": ident.uid,
                    "git_hash": ident.git_hash,
                },
            }
        )

        # 2. Capture full settings via the text CLI, then exit cleanly.
        self._emit({"type": "step", "step": "cli", "status": "running"})
        cli_outputs = fc.run_cli(self.config.settings.cli_commands)
        self._validate_cli(cli_outputs)
        self._emit({"type": "step", "step": "cli", "status": "ok"})

        # 3. Build the normalised snapshot (pilot stays empty for now).
        snapshot = build_snapshot(
            ident,
            cli_outputs,
            captured_at=captured_at,
            allow_diff_fallback=self.config.settings.parse_diff_fallback,
        )

        # 4. Firmware-hash verification.
        self._emit({"type": "step", "step": "firmware_hash", "status": "running"})
        verify_snapshot(snapshot, self._verifier)
        self._emit(
            {
                "type": "step",
                "step": "firmware_hash",
                "status": "ok",
                "approved": snapshot.firmware_hash_approved,
                "source": snapshot.firmware_hash_source,
            }
        )

        # 5. Evaluate rules.
        evaluation = self._engine.evaluate(snapshot)

        # 6. Persist everything — written once, never modified afterwards.
        out_dir = save_capture(
            self.config.settings.log_dir,
            snapshot,
            evaluation,
            cli_outputs,
            captured_at,
            folder_template=self.config.settings.folder_template,
            pilot_fallback=pilot_fallback,
        )

        self._emit(
            {
                "type": "verdict",
                "passed": evaluation.passed,
                "uid": snapshot.uid,
                "captured_at": captured_at,
                "snapshot": snapshot.to_dict(),
                "evaluation": evaluation.to_dict(),
                "path": str(out_dir),
            }
        )
        return snapshot, evaluation, out_dir

    def _validate_cli(self, cli_outputs: dict[str, str]) -> None:
        """Guarantee the dump is complete before we trust it.

        Three independent layers:
          1. Transport/CLI: each command read must END at the CLI prompt, so a
             timeout-truncated read is already rejected in CliSession.command().
          2. Volume: a dump must be present and have enough settings (catches a
             near-empty read).
          3. Terminator: a complete dump/diff ends with its own closing
             statement. Per the firmware source, ``dump``/``dump all`` ends with
             ``save`` (Betaflight) and ``diff`` / all INAV dumps end with
             ``batch end``. If the last line is neither, the stream was cut off
             (even if it happened to stop right after a "# " comment line).
        """
        sources = ["dump all", "dump"]
        if self.config.settings.parse_diff_fallback:
            sources += ["diff all", "diff"]

        dump = ""
        for cmd in sources:
            if cli_outputs.get(cmd):
                dump = cli_outputs[cmd]
                break
        if not dump:
            raise ValueError(
                f"CLI capture incomplete: no dump output (looked for {', '.join(sources)})"
            )

        lines = [ln.strip() for ln in dump.splitlines() if ln.strip()]
        set_lines = sum(1 for ln in lines if ln.startswith("set "))
        if set_lines < 3:
            raise ValueError(
                f"CLI capture incomplete: dump has too few settings ({set_lines} 'set' lines)"
            )

        last = lines[-1].lower() if lines else ""
        if last not in _DUMP_TERMINATORS:
            raise ValueError(
                "CLI capture incomplete: dump does not end with a terminator "
                f"('save' or 'batch end') — last line was {lines[-1]!r} (truncated)"
            )
