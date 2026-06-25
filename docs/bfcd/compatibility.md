# bf-configd compatibility

bf-configd supports an **explicit, tested matrix** of Betaflight firmware
families — never "any dump in any app". A dump whose family is not in the matrix
fails closed (status `unsupported`) rather than being served on a guess.

The matrix lives in `config/bfcd_matrix.yaml` and is consumed by
`drone_check/bfcd/compat.py`. App-compatibility notes come from
<https://betaflight.com/docs/wiki/app>.

| Dump firmware | Backend binary | Configurator / App | Status |
|---|---|---|---|
| Betaflight 4.5.x | `bf-configd-4.5` | 10.10.x / 2025.12.x | MVP |
| Betaflight 4.4.x | `bf-configd-4.4` | 10.10.x | phase 2 |
| Betaflight 4.3.x | `bf-configd-4.3` | 10.10.x / 2025.12.x | phase 2 |
| Betaflight 2025.12.x | `bf-configd-2025.12` | 2025.12.x | phase 3 |

Build/verify status: **every firmware version drone-check ships a SITL build for
is built per version and serves cleanly** via `scripts/build_bfcd.sh` — Betaflight
`4.4.0`, `4.5.0`–`4.5.4` and `2025.12.1`–`2025.12.4` (the scripted derivation's
anchors match both the classic 4.4/4.5 layout and the 2025.x platform-refactor
layout). Each binary is built from its own release tag and was verified
end-to-end — reads answered, the craft name from the dump served, MSP writes
refused — including the legacy-`#` (`4.5.1`) and framed-MSP-CLI (`4.5.4`) paths.
4.3 is out of scope (drone-check ships no SITL build for it either). (The `status`
column above is the roadmap phase, independent of which binaries are built
locally; whether a backend binary exists is checked at runtime **per version**.)

## Family (support axis) vs version (build axis)

The **firmware family** — the first two dot-separated components of the version
(`drone_check.bfcd.metadata.firmware_family`) — is the *support* axis: the matrix
above is keyed by family, and a dump whose family is absent fails closed. One rule
covers both versioning schemes:

- `4.5.3` → family `4.5`
- `2025.12.1` → family `2025.12`

The **binary**, however, is built and cached **per version** (per release tag),
exactly like the SITL cache (`bfcd/<version>/bf-configd.elf`). The binary that
serves a dump is always built from that dump's own tag, so its CLI dialect and
config schema match faithfully — in particular across the **4.5.4 framed-CLI
boundary** (≥ 4.5.4 drives the CLI through the framed MSP-CLI; earlier versions
use the raw `#` prompt), which a single per-family 4.5 binary could not bridge.
This supersedes the earlier "families may be merged later" idea (plan §14): for an
inspection tool we build per version to guarantee faithfulness.

## Target context

A faithful Configurator view needs the right target context (board info,
resources, timers, DMA, feature flags, sensor and OSD/VTX capabilities depend on
the target/build). The rule (plan §5.3):

- **Known target** → load the matching Betaflight target context (`native`).
- **Unknown target** → a generic `CONFIGD_GENERIC` context, clearly marked
  *best effort* in the UI (selection `target_context = generic` + a warning).
- Resources present in the dump can partially improve the generic context.

## Selection statuses

`drone_check/bfcd/compat.py` maps a dump to one of:

- **mvp** — primary, tested family. Serve with confidence.
- **planned** — in the matrix, backend defined, not yet proven; serveable but
  flagged.
- **unsupported** — not Betaflight, or family absent from the matrix; not served.
