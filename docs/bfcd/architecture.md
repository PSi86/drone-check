# bf-configd architecture

bf-configd is a read-only **snapshot emulator** for a Betaflight `dump all`. It
loads the dump into real Betaflight CLI/config/MSP code — built per firmware
version with the flight loop removed — and answers the Configurator over MSP, so
the real Configurator renders the config exactly as the drone's owner would see
it. It does **not** reimplement Betaflight semantics (that is the whole point):

```
Betaflight dump all
        ↓  version / target / MSP-API detection        (drone_check/bfcd/metadata.py)
        ↓  backend selection from the matrix            (drone_check/bfcd/compat.py)
        ↓  original Betaflight CLI parses the dump       (native CONFIGD backend)
        ↓  Betaflight PG/config structures in RAM        (native)
        ↓  original MSP handlers answer requests         (native)
        ↓  MSP over WebSocket                            (native bridge / wrapper)
   Betaflight Configurator / App
```

## Two halves

**Python side (this iteration, in `drone_check/bfcd/`)** — the wrapper and
tooling that does *not* need Betaflight code:

- `metadata.py` — detect firmware family, target, board and MSP API from the
  dump (reuses `drone_check.parser`).
- `compat.py` + `config/bfcd_matrix.yaml` — map a dump to a backend with an
  honest status; fail closed on anything unproven.
- `msp.py` — MSP v1/v2 frame codec (encode requests, decode responses).
- `commands.py` — the MSP command matrix as data.
- `probe.py` — send commands to any MSP endpoint, collect raw replies.
- `goldens.py` + `config/bfcd_msp_masks.yaml` — compare responses against SITL,
  masking dynamic fields.
- `session.py` — the integration seam: detect → select → resolve binary →
  (later) launch and bridge to the Configurator.

**Native side (`bf-configd/`, `scripts/build_bfcd.sh`)** — official Betaflight
built with a small read-only guard. Implemented for the 4.5 family:

- Derived from the SITL host target with `-DCONFIGD` (the `CONFIGD` token becomes
  a `-D` define via the Makefile — no Makefile surgery). The derivation is
  scripted in-place on a fresh official checkout, like the SITL build.
- **Read-only guard**: a `#ifdef CONFIGD` gate at the single MSP write chokepoint
  (`mspCommonProcessInCommand`) refuses every MSP in/write command. Reads are
  answered normally; the Configurator cannot change or persist anything. This is
  the one behavioural difference from SITL and the anti-cheat-relevant guarantee.
- The VTX config table is re-enabled (config data only) so `vtxtable` is visible.
- **Flight-loop trimming**: the gyro/filter/PID, accel/attitude and RX tasks are
  gated off, so the scheduler never enters gyro-locked mode (it falls back to
  time-based scheduling, keeping the serial/CLI/MSP task running) and the host
  loop idles at 1 kHz instead of busy-spinning at 20 kHz. ~0.5 % CPU vs ~4.5 %
  for full SITL — ~9× lighter (plan §7.1/8).
- Serving is two-phase like SITL (load the dump over the CLI, `save`+reboot, then
  serve from the populated config) via `drone_check/bfcd/session.py`, bridged to
  an MSP WebSocket (`ws://127.0.0.1:6762`) with websockify (plan §9).
- The web UI offers it next to SITL on `/logs` (plan §BFCD-012).

Deferred (next iterations): re-enabling the OSD stack (SITL `#undef`s it),
runtime stubs beyond SITL's, other families (4.4/4.3/2025.12) and golden tests
vs SITL (BFCD-009).

## Relationship to SITL

bf-configd is the lighter sibling of the existing SITL "view in Configurator"
feature (`drone_check/sitl.py`). SITL boots the whole firmware; bf-configd boots
only config + CLI + MSP. SITL therefore remains the **golden-test oracle**: the
same dump goes into both and their MSP responses are compared (see
`testing.md`). The session layer is built so the web UI can later offer a backend
choice (SITL vs bf-configd) and fall back to SITL for dumps bf-configd cannot
serve (BFCD-012).

## Why native, not a reimplementation

A standalone dump parser + handcrafted MSP payloads would duplicate Betaflight
semantics (CLI ranges, profile context, AUX/mode ranges, serial functions, OSD
positions, the VTX table, target/feature dependencies, per-API MSP layouts) and
need maintenance every firmware release. Building the real Betaflight code keeps
correctness for free; bf-configd only supplies runtime, transport and snapshot
context.
