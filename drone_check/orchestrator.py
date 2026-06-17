"""Per-drone workflow: identify -> ask pilot -> capture -> verify -> evaluate -> store.

The orchestrator is transport-agnostic: it takes a :class:`FlightController`
(real or fake) and drives the full sequence, emitting structured events so any
front-end (web UI, CLI) can render live progress. Each step validates the data
it receives for completeness before moving on.
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
AskPilotFn = Callable[[dict], str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        emit: Optional[EmitFn] = None,
        ask_pilot: Optional[AskPilotFn] = None,
    ):
        self.config = config
        self._emit = emit or (lambda evt: None)
        self._ask_pilot = ask_pilot or (lambda ctx: "")

        self._verifier = FirmwareVerifier(
            allowlist=config.allowlist,
            use_allowlist=config.settings.hash_use_allowlist,
            use_github=config.settings.hash_use_github,
        )
        # Build the rule engine eagerly so a bad rules.yaml fails fast.
        self._engine = RuleEngine(load_rules(config.rules))

    def process(self, fc: FlightController) -> tuple[DroneSnapshot, Evaluation, Path]:
        """Run the full workflow for one connected flight controller."""
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

        # 2. Ask the operator for the pilot name (optional).
        pilot = ""
        if self.config.settings.ask_pilot_name:
            self._emit({"type": "need_pilot", "uid": ident.uid})
            pilot = (self._ask_pilot({"uid": ident.uid, "variant": ident.variant}) or "").strip()

        # 3. Capture full settings via the text CLI, then exit cleanly.
        self._emit({"type": "step", "step": "cli", "status": "running"})
        cli_outputs = fc.run_cli(self.config.settings.cli_commands)
        self._validate_cli(cli_outputs)
        self._emit({"type": "step", "step": "cli", "status": "ok"})

        # 4. Build the normalised snapshot.
        snapshot = build_snapshot(
            ident,
            cli_outputs,
            captured_at=captured_at,
            allow_diff_fallback=self.config.settings.parse_diff_fallback,
        )
        snapshot.pilot = pilot

        # 5. Firmware-hash verification.
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

        # 6. Evaluate rules.
        evaluation = self._engine.evaluate(snapshot)

        # 7. Persist everything.
        out_dir = save_capture(
            self.config.settings.log_dir,
            snapshot,
            evaluation,
            cli_outputs,
            captured_at,
        )

        self._emit(
            {
                "type": "verdict",
                "passed": evaluation.passed,
                "snapshot": snapshot.to_dict(),
                "evaluation": evaluation.to_dict(),
                "path": str(out_dir),
            }
        )
        return snapshot, evaluation, out_dir

    def _validate_cli(self, cli_outputs: dict[str, str]) -> None:
        """Completeness check: an authoritative dump must be present and non-trivial.

        Mid-stream truncation is already caught by the CLI prompt detection; this
        guards against the dump command being missing or returning almost nothing.
        """
        sources = ["dump all", "dump"]
        if self.config.settings.parse_diff_fallback:
            sources += ["diff all", "diff"]
        for cmd in sources:
            text = cli_outputs.get(cmd, "")
            set_lines = sum(1 for line in text.splitlines() if line.strip().startswith("set "))
            if set_lines >= 3:
                return
        raise ValueError(
            "CLI capture incomplete: no usable settings dump received "
            f"(looked for {', '.join(sources)})"
        )
