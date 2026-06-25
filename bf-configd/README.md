# bf-configd — Betaflight dump snapshot MSP emulator

bf-configd loads a Betaflight `dump all` into **real Betaflight CLI/config/MSP
code** (built per firmware version, with the flight loop stripped out) and
presents it to the Betaflight Configurator over WebSocket, read-only — as if a
real flight controller were attached, but without starting SITL's full runtime.

It is the **preferred, default** backend for drone-check's "view in Configurator"
feature — lighter, faster and read-only — with the full SITL instance kept only
as a **fallback** for the rare captures bf-configd cannot serve. The guiding
principle (from the originating plan) is: **Betaflight interprets the dump itself;
bf-configd only provides runtime stubs, transport and snapshot context.**

## Status: working read-only backend (all shipped Betaflight versions)

bf-configd builds, boots and serves a dump to the Configurator over MSP,
firmware-enforced read-only, for **every Betaflight version drone-check ships a
SITL build for** — `4.4.0`, `4.5.0`–`4.5.4` and `2025.12.1`–`2025.12.4`, each
built from its own release tag (so the CLI dialect and config schema match the
dump, including across the 4.5.4 framed-CLI boundary). Verified on Linux/WSL:
reads answer (e.g. `MSP_FC_VERSION` → 4.5.3); every MSP write (`MSP_SET_*`,
`MSP_EEPROM_WRITE`) is refused.

Native backend (built by `scripts/build_bfcd.sh` from official Betaflight source):

- Derived from the SITL host target with `-DCONFIGD`. The one behavioural change
  vs. SITL is the **read-only guard**: a 6-line gate at the single MSP write
  chokepoint (`mspCommonProcessInCommand`) refuses every MSP in/write command, so
  the Configurator can view everything but cannot change or persist anything.
- The VTX config table is re-enabled (as in the SITL build) so `vtxtable`
  power values/labels are visible.
- The derivation is **scripted** (clone tag → in-place edits → build static), so
  it tracks official Betaflight with no hand-maintained fork.

Serving (`drone_check/bfcd/session.py`, `drone-check bfcd serve <dump>`):

- Two-phase like SITL (load the dump over the CLI, `save`+reboot, then serve from
  the populated config), reusing SITL's transport helpers.
- Bridges MSP to `ws://127.0.0.1:6762` via websockify for the web Configurator.

Implemented and tested (Python side):

- `drone_check/bfcd/metadata.py` — detect firmware family / target / MSP API
  from a dump (reuses drone-check's parser). *(BFCD-002)*
- `drone_check/bfcd/compat.py` + `config/bfcd_matrix.yaml` — pick the backend
  for a dump from an explicit compatibility matrix. *(BFCD-001, §5)*
- `drone_check/bfcd/msp.py` — MSP v1/v2 frame codec for probing and golden tests.
- `drone_check/bfcd/commands.py` — the MSP command matrix as data. *(BFCD-008)*
- `drone_check/bfcd/probe.py` — send MSP commands to any endpoint, collect raw
  replies (the golden-test / bring-up client). *(BFCD-009)*
- `drone_check/bfcd/goldens.py` + `config/bfcd_msp_masks.yaml` — compare MSP
  responses against SITL, masking dynamic fields. *(BFCD-009)*
- `drone_check/bfcd/session.py` — the integration seam: detect → select →
  resolve binary → load and serve over MSP, with a pollable status. *(BFCD-012)*
- Web UI: the `/logs` page shows **one** *Im Configurator* button per capture;
  which backend serves it (bf-configd or SITL) is chosen in config
  (`viewer_backend`, default `bfcd`), never in the UI. *(BFCD-012)*
- CLI: `drone-check bfcd plan <dump.txt>` (selection only) and
  `drone-check bfcd serve <dump.txt>` (run the backend).

The backend also **trims the flight loop** (`-DCONFIGD`): the gyro/filter/PID,
accel/attitude and RX tasks are gated off, so the scheduler never enters
gyro-locked mode (falling back to plain time-based scheduling — the serial/CLI/
MSP task keeps running), and the host loop idles slowly instead of busy-spinning
at 20 kHz. Measured ~0.5 % CPU versus ~4.5 % for full SITL on the same machine
(~9× lighter), which is the point of a config-only snapshot.

Not implemented yet (next iterations):

- OSD tab — SITL `#undef`s `USE_OSD`; re-enabling it is deferred. *(parity with SITL)*
- The 4.3 family and golden tests vs SITL. *(BFCD-009)*

## Build

```bash
bash scripts/build_bfcd.sh 4.5.3 4.4.0 2025.12.2   # one or more git tags
```

Automated from official Betaflight source (see `scripts/build_bfcd.sh`): it
clones the tag, applies the in-place derivation (read-only guard + flight-loop
trim + VTX table + faster CLI poll) and builds a static binary into the cache.
The same scripted anchors apply to both the classic 4.4/4.5 layout and the
2025.x platform-refactor layout; 4.5, 4.4 and 2025.12 are verified working. Then
serve a dump:

```bash
drone-check bfcd serve quad_dump.txt  # -> ws://127.0.0.1:6762 for the Configurator
```

## Distribution

The backends are statically linked, so they run on any Linux/WSL without a
toolchain. Bundle the cached binaries on a build machine and install them on an
inspection machine — no compiler needed there (mirrors `drone-check sitl …`):

```bash
drone-check bfcd list                          # what's cached (per family, static?)
drone-check bfcd package bfcd-bundle.tar.gz    # bundle all cached families (or list some)
drone-check bfcd install bfcd-bundle.tar.gz    # verify checksums + extract into the cache
```

## Layout

```
bf-configd/
├── README.md          # this file
├── patches/           # optional per-family *.patch files (core derivation is
│   └── betaflight-4.5/ #  scripted in scripts/build_bfcd.sh, not .patch files)
└── native/            # placeholder for future host sources (flight-loop trim, …)
```

See `docs/bfcd/` for the architecture, compatibility, MSP command matrix and
testing strategy.

## Licensing

The native backend links Betaflight, which is GPLv3-or-later. Any distributed
binary must ship GPL-compatibly: the Betaflight sources / patch series, the
license texts, and a record of the changes. The Python wrapper is a separate
process; document clearly which components are GPL-based when distributing.
