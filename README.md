# drone-check

Document the firmware version and all settings of a drone (Betaflight / INAV,
optionally KISS Ultra later) and evaluate it against configurable rules.

When a flight controller is plugged in over USB the tool:

1. **Identifies** it over **MSP** (binary): FC variant, firmware version, build
   info (incl. short git hash) and the 96-bit MCU unique id used as the
   *flight-controller serial*.
2. Drops into the text **CLI** and captures the full configuration
   (`version`, `dump all`, `status`), checking each response for completeness,
   then exits cleanly so the FC reboots. `dump all` is used because it lists
   every setting with its absolute value — rule evaluation never has to guess a
   firmware default. (`diff all` is optional; add it to `cli_commands` if you
   also want the portable human-readable backup stored.)
3. **Normalises** the data — in particular the VTX power configuration
   (armed / disarmed power and the radio switches that select it).
4. **Verifies the firmware hash** against a local allowlist and/or the official
   firmware GitHub repository.
5. **Evaluates rules** written in [CEL](https://cel.dev) and shows a green/red
   verdict with the reasons.
6. **Logs** everything into its own folder, by default
   `logs/<timestamp>_<pilot_name>_<craft_name>/`.

The capture never waits on operator input and **logs are written once and never
modified or moved** — they contain only the real data read from the flight
controller. The **pilot and craft names come from the FC** (`pilot_name` /
`craft_name`), which are the single source of truth and also name the folder.

Manual entry is **off by default**. Enable `allow_manual_pilot` to let the
operator set a *fallback* pilot name that is used only for the folder label when
the FC reports none — it never alters the captured data files. The folder naming
is configurable via `folder_template`.

## Requirements

- **Python 3.10+**
- A flight controller exposed as a **USB serial (CDC/VCP)** port. On Windows the
  STM32 VCP driver ships with the OS; on Linux the user must be in the `dialout`
  group to access `/dev/ttyACM*`.
- Close **Betaflight / INAV Configurator** before running — it holds the serial
  port open.

## Install

```bash
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
```

```bash
# Linux / macOS
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the `drone-check` command. (Equivalently you can always run
`python -m drone_check <command>`.)

## Run

```bash
# Local web UI + USB hot-plug watcher
drone-check serve                 # http://127.0.0.1:8000

# Try the UI without hardware (then click "Run demo")
drone-check serve --demo

# List serial ports (find your flight controller's COMx)
drone-check ports

# Bring-up: identify + raw CLI dump, no rules (great for first contact)
drone-check probe COM5 --debug --raw

# Capture a single drone on a known port and print the report
drone-check inspect COM5

# Run the whole pipeline against built-in sample drones
drone-check demo
```

### Commands

| Command | What it does |
|---------|--------------|
| `serve [--host H] [--port P] [--demo]` | Start the local web UI and the USB hot-plug watcher. `--demo` skips the watcher (use the "Run demo" button). |
| `ports` | List available serial ports with VID:PID — find your FC's `COMx`. |
| `probe <port> [--raw]` | First-contact bring-up: MSP identify + raw CLI dump, **no** rule evaluation. |
| `inspect <port>` | Full capture + hash check + rule evaluation; prints the report. Exit code `0` = PASS, `2` = FAIL. |
| `demo` | Run the pipeline against the built-in sample drones (no hardware). |

Serial flags for `probe` / `inspect`: `--baud N`, `--connect-delay S`,
`--debug` (tee raw traffic to `./debug/<port>-<time>.log`). Global: `--config DIR`.

The everyday workflow is `drone-check serve`: open the page, plug a drone in —
it is read automatically and a green/red verdict appears; the pilot and craft
names come from the drone. Unplug and repeat.

For the first run against real hardware, follow [HARDWARE_TEST.md](HARDWARE_TEST.md).

## Output

Every capture is written once into its own immutable folder (default template
`{timestamp}_{pilot_name}_{craft_name}`):

```
logs/<timestamp>_<pilot_name>_<craft_name>/
    snapshot.json      normalised firmware + VTX + names + all settings
    evaluation.json    rule results + overall verdict
    report.txt         human-readable summary
    raw/<command>.txt  raw output of each captured CLI command
```

A per-session application log (`logs/session-<timestamp>.log`) records USB/COM
port actions, warnings, errors and successful captures. The web UI shows the
same entries in a live "Session log" list (newest first, length configurable via
`log_list_length`). The header always reflects the *current* state — after an
unstable connection it returns to "Ready" once the drone is removed.

## Configuration

All config lives in `config/`:

| File | Purpose |
|------|---------|
| `settings.yaml` | log dir, folder template, manual-pilot toggle, baud rate, CLI commands, hash-check toggles |
| `rules.yaml` | CEL rules; a drone passes only if every `critical` rule passes |
| `firmware_allowlist.yaml` | approved git hashes per variant + version |

### Rule bindings (CEL)

```
drone.pilot_name, drone.craft_name           (read from the FC)
drone.firmware.{variant,version,target,git_hash,...}
drone.vtx.{power_armed_max_mw,power_disarmed_mw,low_power_disarm,
           switches[].{aux_channel,power_index,reachable_mw[]}}
drone.settings["<cli_setting_name>"]
checks.firmware_hash_approved
```

Example (both armed and disarmed VTX power must stay at/below 25 mW):

```yaml
- id: vtx-power-armed-max
  severity: critical
  expr: 'drone.vtx.power_armed_max_mw <= 25'
```

## VTX power model

**Betaflight** puts VTX power on a switch through `vtx` control lines (not
`adjrange`), mapping an AUX channel + PWM range to a power *index*; the
index→mW mapping comes from `vtxtable powervalues`. `vtx_low_power_disarm`
forces the lowest power while disarmed.

**INAV** has no `vtx` control lines — the Programming Framework drives power via
`logic` conditions with operation `25` ("Set VTx Power Level"). The commanded
value may be a constant (operand type `0`), an RC channel (type `1`, i.e. a
pilot switch/pot) or another logic condition (type `4`, traced back to its RC
channel). INAV operation-25 values are 0-based, one below the `vtx_power`
setting.

Because the live switch position is unknown on the bench, the reported **armed**
power is the maximum any switch position can select (a dynamically driven level
is treated as reaching the whole table) — no position may exceed the limit.

### Real power vs. the OSD label (anti-cheat)

`vtxtable powervalues` are the numbers sent to the VTX; their unit depends on the
protocol, while `vtxtable powerlabels` are free-form OSD strings that can be set
to anything. A cheater can label a 400 mW level "25" to read 25 mW on the OSD
while transmitting 400 mW. drone-check decodes the **real** power from the value
and flags any level whose label understates it (`osd_power_mismatch`).

The encoding is determined by the VTX **device type** (from MSP `MSP_VTX_CONFIG`:
`SmartAudio` / `Tramp` / `RTC6705` / `MSP`) plus the value pattern:

| Protocol | `powervalues` | Verifiable? |
|----------|---------------|-------------|
| IRC Tramp | milliwatts (25 100 200 400 600) | yes |
| SmartAudio **2.1** | dBm (14 20 26 36 → 25/100/400/4000 mW) | yes |
| SmartAudio **V1/V2** | opaque power **indices** (0 1 2 3) | **no** |
| RTC6705 | milliwatts | yes |

The exact VTX type comes from MSP; it is **not** in the text dump. The SmartAudio
sub-version is not exposed by the FC at all, so it is inferred from the value
encoding. For **index-based** tables (SmartAudio V1/V2) the real mW lives only in
the device and the manipulable label, so it **cannot be verified** from the FC —
drone-check marks such captures `power_verifiable: false` and fails the
`vtx-power-verifiable` rule rather than trusting the label.

## Firmware allowlist

`config/firmware_allowlist.yaml` is generated from the official release tags:

```bash
python scripts/update_allowlist.py            # refresh from GitHub
python scripts/update_allowlist.py --min-btfl 4.4.0 --min-inav 7.0.0
```

Each entry is a release tag's full commit SHA; the firmware reports an
abbreviation of it, which `drone-check` matches by prefix. Set `GITHUB_TOKEN`
to raise the API rate limit. Manual entries (e.g. approved custom builds) can be
added by hand and survive as long as you don't regenerate.

## Tests

```bash
pytest
```

The parser, VTX normalisation, capture assembly and rule engine are covered by
tests using built-in sample drones, so the full pipeline is verifiable without
hardware.
