# bf-configd testing

## Unit tests (now, no hardware / no SITL)

The Python side is covered by the `tests/test_bfcd_*.py` suite, which runs as
part of the normal `pytest`:

- `test_bfcd_metadata.py` — family derivation, Betaflight vs non-Betaflight,
  warnings for incomplete dumps.
- `test_bfcd_compat.py` — backend selection (mvp / planned / unsupported), target
  context fallback, loading the real matrix.
- `test_bfcd_msp.py` — MSP v1/v2 frame codec round-trips, CRCs, partial-frame
  handling, and the probe driven end-to-end over a loopback transport.
- `test_bfcd_goldens.py` — exact vs masked payload comparison, masking of
  dynamic byte ranges, loading the real masks.
- `test_bfcd_commands.py` — the MSP command matrix is internally consistent
  (unique codes/names, blocked writes excluded from probe lists).

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_bfcd_*.py -q
```

Hardware-free smoke test of the wrapper:

```bash
.\.venv\Scripts\python.exe -m drone_check bfcd plan <dump.txt>
```

## Native backend verification (manual, Linux/WSL)

The 4.5 backend is verified by building it and driving it with the probe:

```bash
bash scripts/build_bfcd.sh 4.5.3            # build into the cache
drone-check bfcd serve some_dump.txt        # two-phase load + serve
# from another shell, the bfcd probe against the serve-phase TCP port confirms:
#  - reads answer (MSP_API_VERSION/FC_VARIANT/FC_VERSION),
#  - the read-only guard refuses MSP writes (MSP_SET_*, MSP_EEPROM_WRITE -> error),
#  - websockify is listening on ws://127.0.0.1:6762.
```

## Golden tests against SITL (next iteration)

SITL stays the reference oracle. Per supported family, the loop (plan §13):

1. Build/start SITL for the version (the existing `scripts/build_sitl.sh` +
   `drone_check/sitl.py` already do this).
2. Load the dump into SITL via its CLI.
3. Send the MSP command list (`drone_check/bfcd/probe.py`) and save the raw
   response frames.
4. Start bf-configd with the same dump, send the same list, save its frames.
5. Compare with the per-command masks (`drone_check/bfcd/goldens.py` +
   `config/bfcd_msp_masks.yaml`): `exact` for deterministic commands, `masked`
   for those with runtime-dynamic fields (uptime, cpu load, sensor/arming flags,
   battery runtime).

The probe and the comparison are already implemented and unit-tested; wiring the
SITL-vs-bf-configd harness waits on the native backend existing.

## Test dumps (plan §13.3)

Aim to cover: an empty default dump per version, a real 5" freestyle dump, a
whoop dump, an HDZero/Walksnail/DJI VTX+OSD dump, a GPS dump, a custom resource
remap, a VTX table, multiple PID profiles, multiple rate profiles, and complex
modes/AUX cases. drone-check's existing `demo.py` dumps are a starting point.
