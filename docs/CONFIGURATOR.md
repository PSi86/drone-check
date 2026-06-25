# Logs page & "view in Configurator"

The web UI (`drone-check serve`) has a second page at **`/logs`** (link in the
header) that lists every capture in the log directory and lets an inspector
re-open any of them — including viewing the exact configuration in the real
Betaflight Configurator.

> **Which backend?** The "View in Configurator" view can be served by two
> backends. **bf-configd is the preferred, default backend** (lighter and
> read-only); **SITL is a fallback** for the rare captures bf-configd cannot
> serve. This document covers the **SITL** backend specifically — for bf-configd
> see **[docs/bfcd/](bfcd/)**. Switch backends with `viewer_backend` in
> `settings.yaml` (`bfcd` default, or `sitl`).

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

SITL binaries are **built from the firmware source per version** (the SITL binary
is a Linux host process, not a Windows `.exe`). drone-check itself never builds —
it only selects a pre-built binary from a cache. This is the agreed approach; see
[the SITL background](#why-sitl-via-wsl) below.

**Platform:** on **Windows** the Linux binaries run under **WSL**; on **Linux**
they run **natively** (no WSL — `sitl.distro` is ignored). The steps below cover
the Windows/WSL setup; on Linux you just build (or install a bundle) and run.

1. Install WSL with a distro (default: `Ubuntu`).
2. Install the build toolchain **once**, inside WSL:

   ```bash
   sudo apt-get update && sudo apt-get install -y build-essential ruby git
   ```

3. Pre-build the SITL binaries for the firmware versions you inspect (run inside
   WSL, from the repo):

   ```bash
   bash scripts/build_sitl.sh 4.4.0 4.5.4 2025.12.2
   ```

   `<version>` is a Betaflight git tag. Both the old semver tags (e.g. `4.4.0`)
   and the newer date-based tags (e.g. `2025.12.2`) are supported — the script
   adapts to the source-tree layout each firmware generation uses (see the
   patches table below).

   Each version is cloned, patched and built into
   `~/.cache/drone-check/sitl/<version>/betaflight_SITL.elf`. The first build of a
   version downloads the ARM SDK and compiles, so it takes a few minutes;
   afterwards it is cached and reused.

`websockify` is a Python dependency (installed with the project) — it bridges the
WebSocket-only web Configurator to SITL's TCP port.

### What `build_sitl.sh` patches

The stock SITL target is meant for the gazebo simulator and is missing things an
inspector needs. The script applies these source patches before building:

| Patch | Why |
|-------|-----|
| `-Werror` → `-Wno-error` | Modern host GCC is far newer than the (often years-old) sources and would otherwise fail the build on new warnings. |
| Enable `USE_VTX_COMMON` / `USE_VTX_CONTROL` / `USE_VTX_TABLE` | Stock SITL compiles VTX out, so the `vtxtable` (the anti-cheat-relevant power values/labels) would be invisible. The table is pure config — no VTX device is needed to show it. |
| `dyad_setUpdateTimeout(0.5f)` → `0.01f` | SITL's TCP poll timeout otherwise flushes the CLI echo only ~twice a second, throttling the dump load to ~45 s. 0.01 s matches the 100 Hz serial task. (Newer firmware already ships `0.01f`, so this is a no-op there.) |

The SITL source tree was reorganised between firmware generations, so the script
locates each patch target adaptively: 4.4.x keeps SITL under
`src/main/target/SITL/`, while 2024.x+ (incl. the date-based `2025.x` releases)
moved it to `src/platform/SIMULATOR/`.

It also performs two build prerequisites automatically:

- **`make configs`** — newer firmware (2024.x+) keeps board configs in a separate
  repo pulled in as the `src/config` git submodule, and the build refuses to
  start until it is hydrated. SITL needs no board config, but the Makefile
  structurally requires the directory to exist.
- **`make arm_sdk_install`** — the build validates the ARM toolchain even for the
  host SITL target. The SDK is fetched into the repo's `tools/` dir (no `sudo`,
  no `PATH` change). The rule lives in `make/tools.mk` (4.4.x) or `mk/tools.mk`
  (2024.x+), so the script searches the top `Makefile` and both dirs.

The binaries are linked **statically** (`OPTIONS=SITL_STATIC` on 2024.x+; 4.4.x
links static by default), so each one carries its own libc and runs on any Linux
/ WSL distro regardless of its glibc version — which is what makes the cache
distributable (see below).

## Distributing the pre-built binaries

You only need a build toolchain (and the minutes-long compile) on the machine
that *builds* the binaries. To run the feature on another machine you just need
the tiny pre-built binaries plus WSL — no toolchain, no source, no internet.

What a target machine needs:

- **WSL** with any glibc-based distro (e.g. `Ubuntu`). The statically-linked
  binaries don't depend on the distro's glibc version, so an older distro is
  fine. (`websockify` runs on the Windows side as part of drone-check, so WSL
  only has to *run* the binary and bind a localhost port.)
- The binaries installed into the SITL cache (`sitl.cache_dir`).

Workflow:

```powershell
# On the build machine: build once (static), then bundle the cache.
#   bash scripts/build_sitl.sh 4.4.0 4.5.4 2025.12.2     # inside WSL
drone-check sitl list                                    # what's cached + static?
drone-check sitl package C:\share\sitl-bundle.tar.gz     # all cached versions
#   (or: drone-check sitl package <out> 2025.12.2 4.4.0  # just these)

# Copy sitl-bundle.tar.gz to the target machine, then:
drone-check sitl install C:\path\to\sitl-bundle.tar.gz   # extracts + checksums
drone-check sitl list                                    # confirm
```

The bundle is a `tar.gz` of `<version>/betaflight_SITL.elf` plus a `SHA256SUMS`
manifest; `install` verifies the checksums before the binaries are used. Ten
versions are only a few MB. `sitl list` flags any **dynamic** (non-portable)
binary — rebuild those with the current `build_sitl.sh` so the bundle works on
older target distros.

## Using it

1. `drone-check serve`, open **http://127.0.0.1:8000/logs**.
2. On a capture, click **View in Configurator**. A bar at the bottom shows live
   progress (checking → starting → loading X/Y → saving → starting → ready).
   - First time for a capture: **a few seconds** (mostly the two SITL boots and
     the save/reboot; feeding the whole `dump all` through the CLI is ~1 s).
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

   The CLI dialect matches the firmware, exactly as on real hardware: the legacy
   raw `#` prompt for Betaflight < 4.5.4 / INAV, and the framed MSP-CLI
   (`STX <cmd> LF ETX`) for Betaflight ≥ 4.5.4 and the 2025.x releases, which
   ignore the raw `#` byte.
3. **Serve.** Relaunch SITL in the same directory — it boots from the populated
   eeprom — and start a `websockify` proxy `ws://127.0.0.1:6761 → tcp:<MSP port>`.
   The loaded config decides which UART carries MSP, and SITL maps each UART to
   its own TCP port (UART1 = 5761 … UART6 = 5766), so the proxy is pointed at
   whichever port actually answers MSP — not assumed to be UART1.

Progress is exposed via `GET /api/sitl/status` (phase + line counter); the logs
page polls it for the progress bar. `POST /api/sitl/stop` ends the session.

### Loading is flow-controlled, not "pasted at once"

SITL's TCP receive buffer is a 1400-byte ring with **no overflow protection** and
no backpressure on firmware consumption. Pasting the whole ~33 KB dump at once
would overwrite unprocessed bytes and **silently corrupt the loaded config** —
unacceptable for an inspection tool. So the loader sends the dump in chunks under
1400 bytes and waits for the firmware to finish each chunk before sending the
next: on the legacy CLI it waits for the command echo; on the framed CLI it
packs many `LF`-separated commands into one `STX..ETX` frame and waits for that
frame's single closing `ETX` (SITL runs the whole frame, then replies once). A
fixed time-based pause is **not** enough — it can elapse while SITL is still
processing, so the next chunk overruns the ring and config lines (e.g. `serial`)
are silently dropped. Batching the framed CLI also keeps it fast: one command
per frame would be gated by SITL's MSP-poll cadence (~20 ms/command, ~25 s for a
full dump), whereas batched frames load it in ~1 s. Lines SITL always rejects
(`resource` / `timer` / `dma` pin maps) are skipped. Correctness was verified by sampling settings spread
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
| `enabled` | `true` | Master switch for the feature. The **"View in Configurator"** button is shown only when this is on **and** a SITL environment is present — WSL with the configured distro on Windows (checked once at startup, without booting WSL), or natively on Linux. So on a machine without it the button is hidden automatically. |
| `distro` | `Ubuntu` | WSL distro holding the SITL cache (Windows only; ignored on Linux, where SITL runs natively). |
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
