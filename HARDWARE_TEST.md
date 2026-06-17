# Hardware bring-up checklist

First contact with a real Betaflight / INAV flight controller. Work top to
bottom; each step builds on the previous one. Have **one Betaflight** and (if
possible) **one INAV** drone ready.

> Close Betaflight Configurator / INAV Configurator first — they hold the serial
> port open and will block this tool.

## 0. Activate the environment

```powershell
.\.venv\Scripts\activate
```

## 1. Find the port

```powershell
drone-check ports
```

Plug the drone in and run it again — the new entry is your flight controller
(STM32 boards usually show `VID:PID=0483:5740`). Note the `COMx` name.

## 2. Low-level probe (no rules), with raw logging

```powershell
drone-check probe COM5 --debug --raw
```

What to verify:

- **MSP identify** prints a sane `variant` (`BTFL`/`INAV`), `version`, `uid`
  (24 hex chars) and `git_hash`.
- **CLI capture** shows `version`, a long `dump all`, and `status`.
- The raw traffic is saved to `./debug/COM5-<time>.log` — keep it if anything
  looks off.

If `dump all` looks truncated, raise the timeouts (see Troubleshooting) and
re-run.

## 3. Full capture + evaluation

```powershell
drone-check inspect COM5 --debug
```

This asks for the pilot name, captures, verifies the firmware hash, evaluates
the rules and writes `logs/<pilot>/<uid>/<timestamp>/`. Exit code `0` = PASS,
`2` = FAIL.

Confirm:

- `Hash OK : True (via allowlist|github)` for a stock release.
- VTX armed/disarmed power and switch count match the actual configuration.
- `logs/.../report.txt`, `snapshot.json`, `evaluation.json` and `raw/*.txt`
  exist and look right.

## 4. The web UI + hot-plug flow

```powershell
drone-check serve
```

Open <http://127.0.0.1:8000>, then plug the drone in. You should see the steps
light up, the pilot prompt, then a green/red verdict. Unplug and plug the next
drone to confirm the loop continues.

## 5. Capture real fixtures (for regression tests)

The raw CLI output saved under `logs/.../raw/` is exactly what we want as test
fixtures. Copy a real `dump all` for a Betaflight and an INAV drone into
`tests/fixtures/` so we can lock parser/VTX behaviour against real hardware.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `open COMx: ... Access is denied` | Configurator or another app holds the port. Close it. |
| MSP identify times out | Wrong port; or the FC needs more settle time — `--connect-delay 1.0`. Check the `--debug` log for any `$M>` bytes. |
| `incomplete response for command 'dump all'` | Slow link. Raise `cli_idle_timeout`/`cli_max_wait` in `config/settings.yaml` (e.g. 3.0 / 60.0). |
| Garbage `board_name` from MSP | Harmless — the CLI `board_name` line overrides it. |
| Capture stops mid-`dump all` | Send me the `./debug/*.log`; the prompt-detection settle window may need tuning. |
| `Hash OK : False` on a stock release | The reported version isn't in `config/firmware_allowlist.yaml`. Re-run `python scripts/update_allowlist.py`, or the build is custom. |
| Port disappears after capture | Expected — `exit` reboots the FC. The hot-plug watcher waits for the unplug. |

### Useful knobs (`config/settings.yaml`)

```yaml
connect_delay: 0.3      # ↑ if MSP identify is flaky right after plug-in
cli_idle_timeout: 1.5   # ↑ for slow links / truncated dump all
cli_max_wait: 30.0      # absolute cap per command
debug_dir: debug        # uncomment to always record raw traffic in serve mode
```
