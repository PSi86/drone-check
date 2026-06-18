# Logs page & "view in Configurator"

The web UI (`drone-check serve`) has a second page at **`/logs`** (link in the
header) that lists every capture in the log directory and lets an inspector
re-open any of them — including viewing the exact configuration in the real
Betaflight Configurator.

## The logs page

`/logs` lists every capture folder under `log_dir`, newest first. It is:

- **searchable** — free-text over pilot name, craft name, UID, firmware version
  and folder name;
- **filterable** — by verdict (PASS / FAIL / no result) and firmware variant.

Each row shows the verdict, capture time, pilot / craft, firmware variant +
version, and the firmware-hash status, plus two actions:

- **Open folder** — opens the immutable capture folder in the OS file manager
  (server-side; safe because `serve` runs locally).
- **View in Configurator** — see below.

The page reads captures back via `GET /api/captures`; capture data on disk is
never modified.

## View in Configurator — what it does

The goal: let the inspector see **exactly what the drone owner would see** if
they connected their drone to the Betaflight Configurator — without the drone,
and with every firmware-version-specific GUI detail handled by the real
Configurator rather than re-implemented by us.

To do that, drone-check loads the capture's `raw/dump_all.txt` into a
**version-matched Betaflight SITL** (Software-In-The-Loop) instance and exposes
it so the Betaflight web Configurator can connect.

```
 capture/raw/dump_all.txt ──load via CLI──▶ Betaflight SITL (WSL, TCP 5761)
                                                   ▲
 Betaflight web Configurator ──ws://…:6761──▶ websockify ──tcp──┘
```

## One-time setup

SITL binaries are **built from the firmware source per version** and run under
**WSL** (the SITL binary is a Linux host process, not a Windows `.exe`).
drone-check itself never builds — it only selects a pre-built binary from a
cache. This is the agreed approach; see
[the SITL background](#why-sitl-via-wsl) below.

1. Install WSL with a distro (default: `Ubuntu`).
2. Install the build toolchain **once**, inside WSL:

   ```bash
   sudo apt-get update && sudo apt-get install -y build-essential ruby git
   ```

3. Pre-build the SITL binaries for the firmware versions you inspect (run inside
   WSL, from the repo):

   ```bash
   bash scripts/build_sitl.sh 4.4.0 4.5.4
   ```

   Each version is cloned, patched and built into
   `~/.cache/drone-check/sitl/<version>/betaflight_SITL.elf`. The first build of a
   version downloads the ARM SDK and compiles, so it takes a few minutes;
   afterwards it is cached and reused.

`websockify` is a Python dependency (installed with the project) — it bridges the
WebSocket-only web Configurator to SITL's TCP port.

### What `build_sitl.sh` patches

The stock SITL target is meant for the gazebo simulator and is missing things an
inspector needs. The script applies three source patches before building:

| Patch | Why |
|-------|-----|
| `-Werror` → `-Wno-error` | Modern host GCC is far newer than the (often years-old) sources and would otherwise fail the build on new warnings. |
| Enable `USE_VTX_COMMON` / `USE_VTX_CONTROL` / `USE_VTX_TABLE` | Stock SITL compiles VTX out, so the `vtxtable` (the anti-cheat-relevant power values/labels) would be invisible. The table is pure config — no VTX device is needed to show it. |
| `dyad_setUpdateTimeout(0.5f)` → `0.01f` | SITL's TCP poll timeout otherwise flushes the CLI echo only ~twice a second, throttling the dump load to ~45 s. 0.01 s matches the 100 Hz serial task. |

It also runs `make arm_sdk_install` for older firmware whose Makefile validates
the ARM toolchain even for the host SITL target.

## Using it

1. `drone-check serve`, open **http://127.0.0.1:8000/logs**.
2. On a capture, click **View in Configurator**. A bar at the bottom shows live
   progress (checking → starting → loading X/Y → saving → starting → ready).
   - First time for a capture: **~10–15 s** (the whole `dump all` is fed through
     SITL's CLI).
   - Same capture again: **near-instant** (the populated eeprom is cached).
3. When ready, the bar shows the connect address. Open the Betaflight web
   Configurator (`app.betaflight.com`), enable **manual connection** and connect
   to **`ws://127.0.0.1:6761`**.
   - `ws://` from an `https://` page is allowed for `127.0.0.1` in Chrome/Edge
     (localhost is exempt from mixed-content blocking).
4. Inspect the configuration. **Stop** the session with the button in the bar
   when done (only one SITL session runs at a time; starting another replaces it,
   and the session is also stopped on server shutdown).

### WSL lifecycle

WSL is **not** touched until you actually use the feature: it is started on the
**first** "View in Configurator" click (the footer shows "Starting WSL…"). It
keeps running between sessions, and is **terminated when drone-check exits** —
but only if drone-check was the one that started it. A WSL instance you already
had running (e.g. for other work) is left alone, and `--terminate <distro>` is
used (never `--shutdown`), so other distros such as `docker-desktop` keep
running. Stopping a single SITL session (the bar's stop button) does **not** stop
WSL — only exiting `serve` does.

## How it works (implementation)

`drone_check/sitl.py` — `SitlRunner` — orchestrates one session at a time:

1. **Select binary.** Picks `<sitl_cache_dir>/<firmware version>/betaflight_SITL.elf`.
   Fails closed with a clear message if WSL is unavailable or that version was
   never built (no silent fallback to a wrong version).
2. **Load (first time only).** SITL reboots on `save` (the process exits), so
   loading is two-phase:
   - start SITL with a fresh `eeprom.bin`, push the dump over the CLI (TCP 5761)
     in flow-controlled chunks, then `save` — SITL writes the eeprom and exits;
   - the populated eeprom is marked loaded and reused next time.
3. **Serve.** Relaunch SITL in the same directory — it boots from the populated
   eeprom — and start a `websockify` proxy `ws://127.0.0.1:6761 → tcp:5761`.

Progress is exposed via `GET /api/sitl/status` (phase + line counter); the logs
page polls it for the progress bar. `POST /api/sitl/stop` ends the session.

### Loading is flow-controlled, not "pasted at once"

SITL's TCP receive buffer is a 1400-byte ring with **no overflow protection** and
no backpressure on firmware consumption. Pasting the whole ~33 KB dump at once
would overwrite unprocessed bytes and **silently corrupt the loaded config** —
unacceptable for an inspection tool. So the loader sends the dump in chunks under
1400 bytes and waits for the CLI echo (which proves SITL drained the buffer)
before sending the next. Lines SITL always rejects (`resource` / `timer` / `dma`
pin maps) are skipped. Correctness was verified by sampling settings spread
across a real dump against the loaded SITL config.

### Caching

The per-capture eeprom is cached under `<sitl_run_dir>/<capture id>/`. A capture
is immutable, so its eeprom never changes — repeat views skip the slow load. The
cache is invalidated automatically when the SITL binary is newer than the cached
eeprom (i.e. after rebuilding with `build_sitl.sh`).

## Limitations

- **Motor / mixer tabs warn.** SITL has no real motor outputs (the `resource`
  pin maps don't apply), so the Configurator's Motors/Mixer tabs show warnings.
  Expected; it does not affect the inspection.
- **VTX device controls are inert.** The VTX *table* (bands, channels,
  powervalues, powerlabels) is shown, but there is no live VTX device, so live
  power control in the VTX tab does nothing. The table — what matters for
  anti-cheat — is present.
- **Only versions you built.** If a capture's firmware version has no cached SITL
  binary, the action fails closed and tells you to run `build_sitl.sh <version>`.
- **drone-check's own analysis stays authoritative for VTX.** The Configurator
  view is a documentation/inspection aid; the green/red verdict and the
  `osd_power_mismatch` / `power_verifiable` logic come from drone-check's own dump
  analysis (see the VTX section in the [README](../README.md)).

## Configuration

Under `sitl:` in `config/settings.yaml`:

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `true` | Master switch for the feature. |
| `distro` | `Ubuntu` | WSL distro holding the SITL cache. |
| `cache_dir` | `~/.cache/drone-check/sitl` | WSL path of the pre-built binaries. |
| `run_dir` | `~/.cache/drone-check/run` | WSL path for per-capture eeprom instances. |
| `tcp_port` | `5761` | SITL UART1 (CLI/MSP). |
| `ws_port` | `6761` | websockify endpoint for the web Configurator. |
| `boot_timeout` | `30.0` | Seconds to wait for SITL to come up. |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "no SITL binary for firmware X" | Build it: `bash scripts/build_sitl.sh X` (inside WSL). |
| "WSL not found" / "distro not available" | Install WSL and the configured distro; check `wsl -l -v`. |
| "websockify proxy did not start" | Ensure the project's deps are installed (`pip install -e .`). |
| Configurator won't connect | Use **manual** connection, address exactly `ws://127.0.0.1:6761`; check the bar shows "ready"; ensure nothing else uses ports 5761/6761. |
| Build fails on a new version | Usually a new compiler warning; `build_sitl.sh` already relaxes `-Werror`. Re-run; check `~/.cache/drone-check/build/`. |

## Why SITL via WSL

There is no downloadable Windows SITL binary; the official path is to build from
source under WSL (a Linux host binary). The Betaflight cloud build server only
offers SITL for recent releases and produces a Linux artifact, so it cannot serve
arbitrary older versions on Windows. Building from the firmware **tag** under WSL
covers any version, which is what `build_sitl.sh` does. WSL2 forwards a port that
SITL binds on `127.0.0.1` to the Windows host, so the host-side loader and the
browser both reach SITL over localhost.
