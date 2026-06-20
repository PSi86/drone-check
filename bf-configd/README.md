# bf-configd — Betaflight dump snapshot MSP emulator

bf-configd loads a Betaflight `dump all` into **real Betaflight CLI/config/MSP
code** (built per firmware version, with the flight loop stripped out) and
presents it to the Betaflight Configurator over WebSocket, read-only — as if a
real flight controller were attached, but without starting SITL's full runtime.

It is a lighter, faster, read-only alternative to the existing SITL-based
"view in Configurator" feature. The guiding principle (from the originating
plan) is: **Betaflight interprets the dump itself; bf-configd only provides
runtime stubs, transport and snapshot context.**

## Status: scaffolding (Python side only)

This directory and the `drone_check/bfcd/` package are the **first iteration**:
everything that can be built and tested without the native backend.

Implemented and tested now (Python):

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
  resolve binary; launching raises a clear "not built yet". *(BFCD-012)*
- CLI: `drone-check bfcd plan <dump.txt>` shows the above for a dump.

Not implemented yet (native, the next iteration):

- The CONFIGD host target and patch series (`patches/`, `native/`). *(BFCD-003)*
- The fake-serial CLI/MSP layer and dump ingest. *(BFCD-004, BFCD-005)*
- The MSP WebSocket endpoint and runtime stubs. *(BFCD-006, BFCD-007)*

## Build (once the native backend exists)

```bash
bash scripts/build_bfcd.sh 4.5.3      # clone official tag, patch, build, cache
```

The build is automated from official Betaflight source — see
`scripts/build_bfcd.sh`. It is wired end-to-end but stops with a clear message
until a family's patch series (below) lands.

## Layout

```
bf-configd/
├── README.md          # this file
├── patches/           # per-family Betaflight patch series (CONFIGD target, ...)
│   └── betaflight-4.5/
└── native/            # host main + runtime stubs added by the patches
```

See `docs/bfcd/` for the architecture, compatibility, MSP command matrix and
testing strategy.

## Licensing

The native backend links Betaflight, which is GPLv3-or-later. Any distributed
binary must ship GPL-compatibly: the Betaflight sources / patch series, the
license texts, and a record of the changes. The Python wrapper is a separate
process; document clearly which components are GPL-based when distributing.
