# Betaflight 4.5 patch series for bf-configd

The **core derivation is scripted** in `scripts/build_bfcd.sh` (in-place edits
applied to a fresh official checkout), not stored as `*.patch` files here — this
mirrors how `scripts/build_sitl.sh` patches SITL and keeps the derivation
version-tolerant. The scripted edits for the 4.5 family are:

- **read-only guard** — a `#ifdef CONFIGD` gate at the single MSP write
  chokepoint (`mspCommonProcessInCommand` in `src/main/msp/msp.c`) that refuses
  every MSP in/write command. This is the one behavioural difference from SITL.
- **VTX config table** — re-enabled in the SITL `target.h` (config data only) so
  `vtxtable` power values/labels are visible.
- **faster CLI poll** — `dyad_setUpdateTimeout` lowered so loading a dump over
  the CLI is fast.
- **flight-loop trim** — the gyro/filter/PID, accel/attitude and RX tasks are
  gated off in `fc/tasks.c` and the host loop idles slowly in `main.c`, so a
  config-only snapshot uses ~9× less CPU than full SITL.

The binary is then built with `make TARGET=SITL OPTIONS="SITL_STATIC CONFIGD"`
(the `CONFIGD` token becomes `-DCONFIGD` via the Makefile — no Makefile surgery).

## Optional extra patches

Any `*.patch` file dropped in this directory is applied (with `git apply`) after
the scripted edits — use this for larger future changes not expressible as the
in-place edits above, e.g.:

- re-enabling the OSD stack for the OSD tab,
- a RAM-only EEPROM so nothing is ever persisted.

Other families (`betaflight-4.4`, `betaflight-2025.12`) follow once 4.5 is
proven against the SITL golden tests.
