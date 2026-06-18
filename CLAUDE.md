# drone-check — guide for AI sessions

## Primary purpose (read this first)

drone-check has **two goals of equal weight**:

1. **Document** a drone's flight-controller firmware and *all* settings faithfully.
2. **Detect impermissible manipulations / cheating** — configurations crafted to
   misrepresent reality at a race or tech inspection (e.g. show "25 mW" on the
   OSD while the VTX actually transmits 400 mW).

This is an inspection / anti-cheat tool. "Faithful documentation" and "catching
deception" are the point — not pilot convenience.

## Your standing mandate as the AI working on this project

You are expected to **actively find weaknesses and build the checks yourself** —
not just implement what is literally requested:

- **Hunt for manipulation vectors.** Wherever a *displayed or claimed* value can
  diverge from the *real configured* value, treat it as a threat to detect. The
  VTX power case (below) is the template: the OSD label is free text while the
  real power is encoded in another field.
- **When you find one, implement the full check** — don't just describe it:
  decode the real value in the parser/normaliser, add a CEL rule in
  `config/rules.yaml`, surface it in the report/snapshot, and add tests plus a
  demo profile that exercises both the honest and the cheating case.
- **Prefer "cannot verify" over trusting a manipulable field.** If the real
  value can't be derived from the FC config (e.g. SmartAudio V1/V2 index tables),
  mark it `power_verifiable = false` and fail closed rather than trusting a label.
- **Ground semantics in authoritative sources** (Betaflight / INAV docs and
  source, protocol specs) before implementing — encodings vary by firmware
  version and VTX protocol.

## Threat-model example already handled (use as a pattern)

VTX output power:
- `vtxtable powervalues` are **dBm** (SmartAudio 2.1), **mW** (IRC Tramp), or
  opaque **indices** (SmartAudio V1/V2). `vtxtable powerlabels` are free-form OSD
  strings — a 400 mW level can be labelled "25".
- We decode the **real** power from the value (dBm→mW), flag any level whose
  label understates it (`osd_power_mismatch`), and mark index-based tables as not
  verifiable. The VTX device type comes from MSP (`MSP_VTX_CONFIG`), which is not
  in the text dump.

Other realised checks: armed/disarmed VTX power ≤ limit incl. all switch
positions (worst case), switch-controlled power detection (Betaflight `vtx`
lines / INAV programming `logic` op 25), firmware-hash allowlist + GitHub.

## Architecture map (where checks live)

- `parser.py` — CLI `dump all` → structured config.
- `vtx.py` — decode real VTX power, detect inconsistencies. New value-decoding /
  anti-manipulation logic usually goes here or in a sibling normaliser.
- `model.py` — `DroneSnapshot` / `VtxConfig` (the data rules evaluate).
- `config/rules.yaml` — **the checks**, as CEL expressions (`critical` fails the
  drone; `warning` is informational). This is where most new checks land.
- `storage.py` — immutable per-capture folder + `report.txt`.
- `firmware.py` — firmware-hash verification; `scripts/update_allowlist.py`.
- `demo.py` / `tests/` — sample drones (honest + cheating) and the test suite.

Web UI / review tooling (not where checks live, but where they are surfaced):

- `server.py` — FastAPI app: live capture page (`/`), logs page (`/logs`),
  WebSocket, and the capture/SITL APIs.
- `captures.py` — read back stored captures for the logs page (read-only;
  path-traversal-safe id resolution; OS file-manager helper).
- `sitl.py` — `SitlRunner`: load a capture into a version-matched Betaflight SITL
  (under WSL) so the real Configurator can inspect it. See
  `docs/CONFIGURATOR.md`.
- `scripts/build_sitl.sh` — pre-builds version-matched SITL binaries (patches in
  the VTX config table + a faster CLI poll timeout). drone-check only selects
  cached binaries, never builds.
- `web/index.html`, `web/logs.html` — the two UI pages (inline HTML/CSS/JS).

## Hard invariants (do not break)

- **Logs are immutable**: written once, never modified or moved; they contain
  only real data read from the flight controller.
- **Identity comes from the FC**: `pilot_name` / `craft_name` are read from the
  drone, never from operator input (manual entry is an optional folder-label
  fallback only, off by default).
- Capture must **never block on operator input**.
- English for code, comments, commits, and docs; chat with the user in German.
- No AI self-attribution anywhere (commits, PRs, comments).

## Workflow

- `pytest` must stay green; add tests for every new check (pass + fail cases).
- `drone-check demo` runs the pipeline against built-in sample drones (no HW).
- `drone-check serve` is the local web UI + USB hot-plug watcher; `/logs` browses
  past captures and opens any in the real Betaflight Configurator via SITL.
- See `README.md` for usage, `docs/CONFIGURATOR.md` for the SITL / Configurator
  feature, and `HARDWARE_TEST.md` for real-hardware bring-up.

## Commands (this project, Windows / PowerShell)

The repo uses an editable install in a local venv at `.venv`. All commands below
assume the project root as the working directory. Prefer `.venv\Scripts\python.exe`
over a bare `python` so the right interpreter is always used.

```powershell
# One-time setup
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# Tests (must stay green)
.\.venv\Scripts\python.exe -m pytest -q

# Run the whole pipeline against built-in sample drones (no hardware)
.\.venv\Scripts\python.exe -m drone_check demo

# List serial ports / first-contact probe / full capture (real hardware)
.\.venv\Scripts\python.exe -m drone_check ports
.\.venv\Scripts\python.exe -m drone_check probe COM5 --debug --raw
.\.venv\Scripts\python.exe -m drone_check inspect COM5

# Refresh the firmware-hash allowlist from official release tags
.\.venv\Scripts\python.exe scripts\update_allowlist.py
```

### Web server (start / verify / stop)

`serve` is long-running, so start it in the background (do NOT block the turn):

```powershell
# start (background) — http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m drone_check serve --host 127.0.0.1 --port 8000

# verify it is up
Invoke-WebRequest -Uri http://127.0.0.1:8000/ -UseBasicParsing -TimeoutSec 2

# stop (free port 8000)
$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($c) { $c.OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force } }
```

After editing `server.py` / `orchestrator.py` (or any Python), **restart** the
server to load the change; `web/index.html` is re-read per request (just reload
the page). `serve --demo` skips the USB watcher (use the "Run demo" button).

Notes:
- `git` push over the native client prints progress to stderr; PowerShell shows
  it as a red `RemoteException` even on success — check the `-> main` line and
  `git status -sb` instead of trusting the colour.
- Commit messages with parentheses/quotes break PowerShell here-strings passed to
  `git commit -m`; write the message to a file and use `git commit -F <file>`.
