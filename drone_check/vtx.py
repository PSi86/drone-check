"""Normalise VTX power configuration for inspection (Betaflight + INAV).

**Switch control.** Betaflight puts VTX power on a switch through ``vtx`` control
lines (not ``adjrange``); INAV uses Programming-Framework ``logic`` conditions
with operation 25 ("Set VTx Power Level"). See the per-firmware normalisers.

**The power table is the subtle part.** ``vtxtable powervalues`` are the numbers
the FC sends to the VTX, and their unit depends on the protocol:

* **SmartAudio 2.1** — the values are **dBm** (e.g. 14/20/26/36). Real power is
  ``10**(dBm/10)`` mW, so 14 dBm = 25 mW, 26 dBm = 400 mW, 36 dBm = 4 W.
* **IRC Tramp** — the values are **milliwatts** directly (25/100/200/400/600).

``vtxtable powerlabels`` are free-form OSD strings ("the label is shown in the
OSD, while the value is sent to the VTX"). They can be set to anything, so a
cheater can label a 400 mW level "25" to read 25 mW on the OSD while actually
transmitting 400 mW. We therefore decode the **real** power from the value and
flag any level whose label understates it (``osd_power_mismatch``).
"""

from __future__ import annotations

import re

from .model import VtxConfig, VtxPowerLevel, VtxSwitch
from .parser import ParsedConfig

# Generic fallback (1-based index -> mW), used only when no `vtxtable` exists.
_DEFAULT_POWER_TABLE = {0: 0, 1: 25, 2: 200, 3: 500, 4: 800, 5: 1000}

# A reported power above the OSD label by more than this factor is treated as a
# manipulation (small tolerance for dBm rounding, e.g. 26 dBm = 398 vs "400").
_UNDERSTATE_TOLERANCE = 1.25

# Above this, a powervalue cannot be dBm for any FPV VTX (40 dBm = 10 W).
_MAX_PLAUSIBLE_DBM = 40

_INAV_OP_SET_VTX_POWER = 25
_OPERAND_VALUE = 0
_OPERAND_RC_CHANNEL = 1
_OPERAND_LOGIC_CONDITION = 4


def dbm_to_mw(dbm: float) -> int:
    return int(round(10 ** (dbm / 10.0)))


def parse_label_mw(label: str) -> int | None:
    """Parse the mW a power label claims. Returns None for MAX/PIT/unparseable.

    Handles plain numbers ("25", "400") and watt notation ("1W6" = 1.6 W,
    "2W" = 2 W, "1.6W").
    """
    s = (label or "").strip().upper()
    if not s or s in ("MAX", "PIT", "OFF", "---", "MIN"):
        return None
    # Watt notation: 1W6 -> 1.6, 2W -> 2.0, 1.6W -> 1.6
    m = re.fullmatch(r"(\d+)W(\d*)", s)
    if m:
        whole = int(m.group(1))
        frac = int(m.group(2)) if m.group(2) else 0
        return int(round((whole + frac / 10.0) * 1000))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*W", s)
    if m:
        return int(round(float(m.group(1)) * 1000))
    m = re.match(r"(\d+(?:\.\d+)?)", s)
    if m:
        return int(round(float(m.group(1))))
    return None


def _looks_like_index(values: list[int]) -> bool:
    """True if values are a small contiguous 0..N or 1..N sequence (SA V1/V2).

    SmartAudio V2.0 tables use plain power-level *indices* (0,1,2,3); the real mW
    lives only in the device and the (manipulable) label, so it is not derivable
    from the FC config.
    """
    if len(values) < 2 or max(values) > 9:
        return False
    if values != sorted(values):
        return False
    start = values[0]
    return start in (0, 1) and values == list(range(start, start + len(values)))


def _infer_unit(values: list[int], label_mws: list[int | None], device_type: str) -> str:
    """Classify powervalues as dbm / mw / index / unknown.

    The VTX *device type* (from MSP) removes the biggest ambiguity: IRC Tramp is
    always mW, so a "25" there is 25 mW, not 25 dBm. For SmartAudio (or an
    unknown device, e.g. dump-only) the encoding is inferred from the values.
    """
    if not values:
        return "unknown"
    if device_type in ("Tramp", "RTC6705"):
        return "mw"
    if _looks_like_index(values):
        return "index"  # SmartAudio V1/V2 — opaque, not verifiable
    if max(values) > _MAX_PLAUSIBLE_DBM:
        return "mw"  # dBm can't exceed ~40 for any FPV VTX
    if label_mws and len(label_mws) == len(values) and label_mws == values:
        return "mw"  # value == label -> both mW (Tramp-style table)
    if any(v == 25 for v in values):
        return "mw"  # 25 mW encoded literally -> mW table
    return "dbm"  # SmartAudio 2.1


def _build_table(cfg: ParsedConfig, device_type: str) -> VtxConfig:
    """Build the decoded power table + per-level OSD honesty check."""
    values = cfg.vtxtable.get("powervalues") or []
    label_strs = [str(x) for x in (cfg.vtxtable.get("powerlabels") or [])]

    if not values:
        # No vtxtable: fall back to a generic index->mW map. This is a guess,
        # so the real power is not independently verifiable.
        return VtxConfig(
            power_table=dict(_DEFAULT_POWER_TABLE),
            power_table_source="default-fallback",
            device_type=device_type,
            power_unit="unknown",
            power_verifiable=False,
        )

    label_mws = [parse_label_mw(s) for s in label_strs]
    unit = _infer_unit(values, label_mws, device_type)
    verifiable = unit in ("dbm", "mw")

    table: dict[int, int | None] = {0: 0}
    levels: list[VtxPowerLevel] = []
    mismatch = False
    for i, value in enumerate(values, start=1):
        if unit == "mw":
            real_mw: int | None = value
        elif unit == "dbm":
            real_mw = dbm_to_mw(value)
        else:
            real_mw = None  # index / unknown -> not derivable
        label = label_strs[i - 1] if i - 1 < len(label_strs) else ""
        label_mw = label_mws[i - 1] if i - 1 < len(label_mws) else None
        understated = (
            real_mw is not None
            and label_mw is not None
            and real_mw > label_mw * _UNDERSTATE_TOLERANCE
        )
        mismatch = mismatch or understated
        table[i] = real_mw
        levels.append(
            VtxPowerLevel(
                index=i,
                raw_value=value,
                real_mw=real_mw,
                label=label,
                label_mw=label_mw,
                understated=understated,
            )
        )

    return VtxConfig(
        power_table=table,
        power_table_source="vtxtable",
        device_type=device_type,
        power_unit=unit,
        power_verifiable=verifiable,
        levels=levels,
        osd_power_mismatch=mismatch,
    )


def _mw_for_index(table: dict[int, int | None], index: int) -> int | None:
    if index <= 0:
        return None
    return table.get(index)


def _all_selectable_mw(table: dict[int, int | None]) -> list[int]:
    return [mw for idx, mw in table.items() if idx >= 1 and mw is not None]


def _apply_disarm(vtx: VtxConfig) -> None:
    if vtx.low_power_disarm in ("ON", "UNTIL_FIRST_ARM"):
        vtx.power_disarmed_mw = _mw_for_index(vtx.power_table, 1)
    else:
        vtx.power_disarmed_mw = vtx.power_armed_max_mw


def normalise_vtx(
    cfg: ParsedConfig, variant: str = "BTFL", device_type: str = "unknown"
) -> VtxConfig:
    """Derive a :class:`VtxConfig`, dispatching on firmware variant."""
    if (variant or "").upper() == "INAV":
        return _normalise_inav(cfg, device_type)
    return _normalise_betaflight(cfg, device_type)


def _global_power(vtx: VtxConfig, cfg: ParsedConfig) -> list[int]:
    """Set the global power index/mW on ``vtx``; return it as a candidate list."""
    raw = cfg.settings.get("vtx_power")
    if raw is None:
        return []
    try:
        gi = int(raw)
    except ValueError:
        return []
    vtx.power_global_index = gi
    mw = _mw_for_index(vtx.power_table, gi)
    vtx.power_global_mw = mw
    return [mw] if mw is not None else []


def _normalise_betaflight(cfg: ParsedConfig, device_type: str = "unknown") -> VtxConfig:
    vtx = _build_table(cfg, device_type)
    vtx.low_power_disarm = cfg.settings.get("vtx_low_power_disarm", "OFF").upper()
    candidate_mw = _global_power(vtx, cfg)

    # vtx <index> <aux_channel> <band> <channel> <power> <pwm_start> <pwm_end>
    # `dump all` lists every control slot (Betaflight has 10), most of them empty
    # (`vtx N 0 0 0 0 0 0`). Only count a slot that actually switches *power*:
    # it must have a real PWM range AND select a power level (power_index > 0;
    # 0 means "leave power unchanged", i.e. a band/channel-only switch).
    for nums in cfg.vtx_lines:
        _index, aux_channel, _band, _channel, power_index = nums[:5]
        pwm_start = nums[5] if len(nums) > 5 else 0
        pwm_end = nums[6] if len(nums) > 6 else 0
        if pwm_end <= pwm_start or power_index <= 0:
            continue  # empty/inactive slot or not a power switch
        mw = _mw_for_index(vtx.power_table, power_index)
        vtx.switches.append(
            VtxSwitch(
                aux_channel=aux_channel,
                pwm_start=pwm_start,
                pwm_end=pwm_end,
                power_index=power_index,
                reachable_mw=[mw] if mw is not None else [],
            )
        )
        if mw is not None:
            candidate_mw.append(mw)

    vtx.power_armed_max_mw = max(candidate_mw) if candidate_mw else None
    _apply_disarm(vtx)
    return vtx


def _trace_rc_channel(logic_lines: list[list[int]], lc_index: int, depth: int = 0) -> int | None:
    if depth > 3:
        return None
    for nums in logic_lines:
        if nums[0] != lc_index:
            continue
        op_a_type, op_a_val = nums[4], nums[5]
        if op_a_type == _OPERAND_RC_CHANNEL:
            return op_a_val
        if op_a_type == _OPERAND_LOGIC_CONDITION:
            return _trace_rc_channel(logic_lines, op_a_val, depth + 1)
        return None
    return None


def _normalise_inav(cfg: ParsedConfig, device_type: str = "unknown") -> VtxConfig:
    vtx = _build_table(cfg, device_type)
    vtx.low_power_disarm = cfg.settings.get("vtx_low_power_disarm", "OFF").upper()
    candidate_mw = _global_power(vtx, cfg)

    for nums in cfg.logic_lines:
        _rule, enabled, _activator, operation = nums[0], nums[1], nums[2], nums[3]
        op_a_type, op_a_val = nums[4], nums[5]
        if operation != _INAV_OP_SET_VTX_POWER or not enabled:
            continue

        if op_a_type == _OPERAND_VALUE:
            # Constant power: INAV op-25 value is 0-based -> 1-based table index.
            index = op_a_val + 1
            mw = _mw_for_index(vtx.power_table, index)
            reachable = [mw] if mw is not None else []
            channel, power_index = None, index
        elif op_a_type == _OPERAND_RC_CHANNEL:
            reachable = _all_selectable_mw(vtx.power_table)
            channel, power_index = op_a_val, -1
        else:
            reachable = _all_selectable_mw(vtx.power_table)
            channel, power_index = _trace_rc_channel(cfg.logic_lines, op_a_val), -1

        vtx.switches.append(
            VtxSwitch(
                aux_channel=channel if channel is not None else -1,
                pwm_start=0,
                pwm_end=0,
                power_index=power_index,
                reachable_mw=reachable,
            )
        )
        candidate_mw.extend(reachable)

    vtx.power_armed_max_mw = max(candidate_mw) if candidate_mw else None
    _apply_disarm(vtx)
    return vtx
