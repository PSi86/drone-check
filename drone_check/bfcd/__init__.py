"""bf-configd — a read-only Betaflight ``dump all`` snapshot MSP emulator.

bf-configd loads a Betaflight ``dump all`` into *real* Betaflight CLI/config/MSP
code (built per firmware version, the flight loop stripped out) and presents it
to the Betaflight Configurator over WebSocket, as if a real flight controller
were attached — but read-only and without starting SITL's full runtime.

This package is the **Python side** of that design (the integration seam inside
drone-check): dump-metadata detection, backend/version selection, the MSP frame
codec used by the probe and golden-test tooling, and the session skeleton that
will drive the native backend once it is built. The native CONFIGD backend
itself (a patched, official Betaflight build) is produced out-of-tree by
``scripts/build_bfcd.sh`` and is not part of this package.

See ``docs/bfcd/architecture.md`` for the full design and
``betaflight_dump_snapshot_msp_emulator_plan.md`` for the originating plan.
"""

from __future__ import annotations

from .metadata import DumpMetadata, detect_metadata, firmware_family
from .compat import BackendSelection, BfcdStatus, load_matrix, select_backend
from .msp import (
    BfcdMspError,
    MspFrame,
    crc8_dvb_s2_buf,
    decode_frame,
    encode_request,
)

__all__ = [
    "DumpMetadata",
    "detect_metadata",
    "firmware_family",
    "BackendSelection",
    "BfcdStatus",
    "load_matrix",
    "select_backend",
    "BfcdMspError",
    "MspFrame",
    "crc8_dvb_s2_buf",
    "decode_frame",
    "encode_request",
]
