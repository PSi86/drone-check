"""Pick the backend for a dump from the compatibility matrix (BFCD-001 / §5).

The plan is explicit that bf-configd does **not** try to serve "any dump in any
app"; it supports an explicit, tested matrix of firmware families. This module
loads that matrix (``config/bfcd_matrix.yaml``) and maps a detected
:class:`~drone_check.bfcd.metadata.DumpMetadata` onto the backend that would
serve it, together with an honest status so the UI can fail closed on anything
unproven.

``select_backend`` is a pure function (no filesystem / WSL access): it decides
*which* backend a dump wants. Whether that backend binary actually exists on
disk is a separate, environment-dependent check handled by the session layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from .metadata import DumpMetadata


class BfcdStatus(str, Enum):
    """How confidently bf-configd can serve a given firmware family."""

    MVP = "mvp"              # primary, tested target family (e.g. 4.5.x)
    PLANNED = "planned"      # in the matrix but a later phase, not yet proven
    UNSUPPORTED = "unsupported"  # not Betaflight, or family not in the matrix


@dataclass
class BackendSelection:
    """The backend a dump maps to, with the rationale and any caveats."""

    status: BfcdStatus
    family: str = ""
    backend: str = ""            # backend binary base name, e.g. "bf-configd-4.5"
    app_compat: str = ""         # human-readable Configurator/App compatibility note
    target_context: str = "generic"  # "native" if the dump names a target, else "generic"
    warnings: list[str] = field(default_factory=list)

    @property
    def serveable(self) -> bool:
        """Whether it is meaningful to *attempt* serving this dump at all.

        ``PLANNED`` families are serveable in principle (a backend is defined),
        even though the binary may not be built yet; ``UNSUPPORTED`` is not.
        """
        return self.status is not BfcdStatus.UNSUPPORTED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "family": self.family,
            "backend": self.backend,
            "app_compat": self.app_compat,
            "target_context": self.target_context,
            "warnings": list(self.warnings),
        }


_DEFAULT_MATRIX_NAME = "bfcd_matrix.yaml"


def load_matrix(config_dir: Path) -> dict:
    """Load the family compatibility matrix; returns ``{}`` if absent."""
    path = Path(config_dir) / _DEFAULT_MATRIX_NAME
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    families = doc.get("families", {}) if isinstance(doc, dict) else {}
    return families if isinstance(families, dict) else {}


def select_backend(metadata: DumpMetadata, matrix: dict) -> BackendSelection:
    """Map detected metadata to a backend and an honest serve status.

    Decision order (fail closed):

    1. Not Betaflight or no family -> ``UNSUPPORTED``.
    2. Family not in the matrix -> ``UNSUPPORTED`` (we don't guess across lines).
    3. In the matrix -> ``MVP`` for the primary family, else ``PLANNED``; the
       backend name and app-compat note come straight from the matrix.

    Target context is ``native`` when the dump names a target/MCU (the right
    board context can be loaded), otherwise ``generic`` with a best-effort
    warning, mirroring the plan's §5.3 rule.
    """
    sel = BackendSelection(status=BfcdStatus.UNSUPPORTED, family=metadata.firmware_family)
    sel.warnings.extend(metadata.warnings)

    if not metadata.is_betaflight:
        variant = metadata.variant or "unknown"
        sel.warnings.append(f"variant {variant!r} is not supported by bf-configd")
        return sel
    if not metadata.firmware_family:
        sel.warnings.append("no firmware family — cannot select a backend")
        return sel

    entry = matrix.get(metadata.firmware_family)
    if not isinstance(entry, dict):
        sel.warnings.append(
            f"firmware family {metadata.firmware_family} is not in the "
            f"compatibility matrix; no backend is defined for it"
        )
        return sel

    sel.backend = str(entry.get("backend", f"bf-configd-{metadata.firmware_family}"))
    sel.app_compat = str(entry.get("app", ""))
    phase = str(entry.get("status", "planned")).lower()
    sel.status = BfcdStatus.MVP if phase == "mvp" else BfcdStatus.PLANNED

    if metadata.target:
        sel.target_context = "native"
    else:
        sel.target_context = "generic"
        sel.warnings.append("no target in dump — using a generic target context (best effort)")

    return sel
