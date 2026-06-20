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

**Native side (next iteration, `bf-configd/`)** — a patched, official Betaflight
build:

- A host `CONFIGD` target derived from SITL, without the gyro/PID/motor/scheduler
  runtime (plan §7.1, BFCD-003).
- A fake serial port (RAM RX/TX rings) so the *existing* CLI and MSP serial state
  machines drive unchanged — minimally invasive (plan §7.2/7.3, BFCD-004).
- Dump ingest over the original CLI path, with dangerous commands blocked
  (BFCD-005).
- Runtime stubs for everything MSP reads that is not config: time, sensors,
  battery, motors, storage (plan §8, BFCD-007).
- An MSP WebSocket endpoint (`ws://127.0.0.1:6762` by default) so the web
  Configurator connects exactly as it does to SITL (plan §9, BFCD-006).

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
