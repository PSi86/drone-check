# Betaflight 4.5 patch series for bf-configd

`scripts/build_bfcd.sh` applies every `*.patch` in this directory (in name
order) to an official Betaflight 4.5.x checkout before building `TARGET=CONFIGD`.
The series is **not implemented yet** — this file documents the intended patches
so the build pipeline has a target to grow into.

Planned patches (mirrors the plan §7 and work packages BFCD-003..005):

- `0001-configd-target.patch` — add a host `CONFIGD` target derived from SITL:
  builds the config/CLI/MSP core, but does not start the gyro/PID/motor/
  scheduler runtime. *(BFCD-003)*
- `0002-fake-serial.patch` — a RAM-backed `serialPort_t` (RX/TX ring buffers) so
  CLI and MSP can be driven without real hardware serial. *(BFCD-004)*
- `0003-msp-ws-host.patch` — a host main that feeds the dump through the CLI on
  startup and bridges MSP frames between the fake serial port and a
  WebSocket/stdio endpoint, plus the read-only guard and runtime stubs.
  *(BFCD-005..007)*

Keep patches minimal and rebasable: prefer additive files in `bf-configd/native/`
over edits to Betaflight sources wherever possible, so the series survives
firmware updates.

Other families (`betaflight-4.4`, `betaflight-2025.12`) follow once 4.5 is
proven against the SITL golden tests.
