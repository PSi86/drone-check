# Native backend sources (added by the patch series)

This directory will hold the host-side C sources the bf-configd patch series
adds to a Betaflight checkout — kept here, outside the Betaflight tree, so they
survive firmware updates and the patches stay small and rebasable.

Planned files (the patch series copies/links them into the build):

- `host/main.c` — host entry point: init Betaflight defaults, feed the dump
  through the CLI, then service MSP over the chosen transport. Never starts the
  flight loop / scheduler.
- `host/transport_ws.c` — WebSocket (and stdio) ↔ fake-serial bridge.
- `stubs/serial_stub.c` — RAM-backed `serialPort_t` (fake serial, RX/TX rings).
- `stubs/sensors_stub.c` — synthetic "not detected" sensor state.
- `stubs/storage_stub.c` — RAM-only EEPROM (no flash/SD writes).
- `stubs/system_stub.c`, `stubs/time_stub.c` — monotonic time, no-op reboot.
- `include/bf_configd.h` — the small C API (`bfcd_apply_cli_dump`,
  `bfcd_process_msp_frame`, …) the host main builds on. See the plan §6.

Nothing here yet — see `../README.md` and `docs/bfcd/architecture.md`.
