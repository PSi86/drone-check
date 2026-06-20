# bf-configd compatibility

bf-configd supports an **explicit, tested matrix** of Betaflight firmware
families — never "any dump in any app". A dump whose family is not in the matrix
fails closed (status `unsupported`) rather than being served on a guess.

The matrix lives in `config/bfcd_matrix.yaml` and is consumed by
`drone_check/bfcd/compat.py`. App-compatibility notes come from
<https://betaflight.com/docs/wiki/app>.

| Dump firmware | Backend binary | Configurator / App | Status |
|---|---|---|---|
| Betaflight 4.5.x | `bf-configd-4.5` | 10.10.x / 2025.12.x | MVP |
| Betaflight 4.4.x | `bf-configd-4.4` | 10.10.x | phase 2 |
| Betaflight 4.3.x | `bf-configd-4.3` | 10.10.x / 2025.12.x | phase 2 |
| Betaflight 2025.12.x | `bf-configd-2025.12` | 2025.12.x | phase 3 |

## Family derivation

The build axis is the **firmware family**: the first two dot-separated
components of the version (`drone_check.bfcd.metadata.firmware_family`). This
covers both versioning schemes with one rule:

- `4.5.3` → family `4.5`
- `2025.12.1` → family `2025.12`

Each patch level of a family shares one backend candidate. The MVP builds exactly
per release tag; families may be merged later once golden tests prove MSP/CLI
compatibility across patch levels (plan §14).

## Target context

A faithful Configurator view needs the right target context (board info,
resources, timers, DMA, feature flags, sensor and OSD/VTX capabilities depend on
the target/build). The rule (plan §5.3):

- **Known target** → load the matching Betaflight target context (`native`).
- **Unknown target** → a generic `CONFIGD_GENERIC` context, clearly marked
  *best effort* in the UI (selection `target_context = generic` + a warning).
- Resources present in the dump can partially improve the generic context.

## Selection statuses

`drone_check/bfcd/compat.py` maps a dump to one of:

- **mvp** — primary, tested family. Serve with confidence.
- **planned** — in the matrix, backend defined, not yet proven; serveable but
  flagged.
- **unsupported** — not Betaflight, or family absent from the matrix; not served.
