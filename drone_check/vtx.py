"""Normalise VTX power configuration for inspection (Betaflight + INAV).

**Betaflight** puts VTX power on a switch through ``vtx`` control lines (not
``adjrange`` — VTX power is not a Betaflight adjustment function). Each line maps
an AUX channel + PWM range to a power *index*; the index->mW mapping comes from
``vtxtable powervalues``. ``vtx_low_power_disarm`` forces the lowest index while
disarmed.

**INAV** has no ``vtx`` control lines. Instead the Programming Framework drives
VTX power via ``logic`` conditions whose *operation* is ``25`` ("Set VTx Power
Level"). The commanded value can be a constant (operand type ``0``), an RC
channel (type ``1``, i.e. a pilot switch/pot), or another logic condition
(type ``4``). INAV operation-25 values are 0-based (0..3 SmartAudio, 0..4 Tramp),
one below the 1-based ``vtx_power`` setting.

Because the live switch position is unknown on the bench, the reported **armed**
power is the maximum any switch position can select: a dynamically driven
(RC-channel) power level is treated as reaching the whole table, so no switch
position may exceed the limit for the drone to pass.
"""

from __future__ import annotations

from .model import VtxConfig, VtxSwitch
from .parser import ParsedConfig

# Generic fallback (1-based index -> mW), used when no `vtxtable` was emitted.
# Index 0 means "unchanged". Values follow the common SmartAudio ordering.
_DEFAULT_POWER_TABLE = {0: 0, 1: 25, 2: 200, 3: 500, 4: 800, 5: 1000}

# INAV "Set VTx Power Level" operation id in the Programming Framework.
_INAV_OP_SET_VTX_POWER = 25
# INAV operand types we care about.
_OPERAND_VALUE = 0
_OPERAND_RC_CHANNEL = 1
_OPERAND_LOGIC_CONDITION = 4


def _build_power_table(cfg: ParsedConfig) -> tuple[dict[int, int], str]:
    """Return (index -> mW, source). vtxtable index 1 maps to powervalues[0]."""
    values = cfg.vtxtable.get("powervalues")
    if values:
        table = {0: 0}
        for i, mw in enumerate(values, start=1):
            table[i] = mw
        return table, "vtxtable"
    return dict(_DEFAULT_POWER_TABLE), "default-fallback"


def _mw_for_index(table: dict[int, int], index: int) -> int | None:
    if index <= 0:
        return None  # 0 == unchanged / not selecting a power
    return table.get(index)


def _all_selectable_mw(table: dict[int, int]) -> list[int]:
    """Every real power (mW) in the table, i.e. the dynamic worst case."""
    return [mw for idx, mw in table.items() if idx >= 1]


def _apply_disarm(vtx: VtxConfig, table: dict[int, int]) -> None:
    """Fill power_disarmed_mw from the low-power-on-disarm setting."""
    if vtx.low_power_disarm in ("ON", "UNTIL_FIRST_ARM"):
        vtx.power_disarmed_mw = _mw_for_index(table, 1)
    else:
        vtx.power_disarmed_mw = vtx.power_armed_max_mw


def normalise_vtx(cfg: ParsedConfig, variant: str = "BTFL") -> VtxConfig:
    """Derive a :class:`VtxConfig`, dispatching on firmware variant."""
    if (variant or "").upper() == "INAV":
        return _normalise_inav(cfg)
    return _normalise_betaflight(cfg)


def _normalise_betaflight(cfg: ParsedConfig) -> VtxConfig:
    table, source = _build_power_table(cfg)
    vtx = VtxConfig(power_table=table, power_table_source=source)
    vtx.low_power_disarm = cfg.settings.get("vtx_low_power_disarm", "OFF").upper()

    candidate_mw: list[int] = []
    global_idx_raw = cfg.settings.get("vtx_power")
    if global_idx_raw is not None:
        try:
            gi = int(global_idx_raw)
        except ValueError:
            gi = 0
        vtx.power_global_index = gi
        gi_mw = _mw_for_index(table, gi)
        vtx.power_global_mw = gi_mw
        if gi_mw is not None:
            candidate_mw.append(gi_mw)

    # vtx <index> <aux_channel> <band> <channel> <power> <pwm_start> <pwm_end>
    for nums in cfg.vtx_lines:
        _index, aux_channel, _band, _channel, power_index = nums[:5]
        pwm_start = nums[5] if len(nums) > 5 else 0
        pwm_end = nums[6] if len(nums) > 6 else 0
        mw = _mw_for_index(table, power_index)
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
    _apply_disarm(vtx, table)
    return vtx


def _trace_rc_channel(logic_lines: list[list[int]], lc_index: int, depth: int = 0) -> int | None:
    """Follow a logic-condition reference to the RC channel that ultimately feeds it.

    Resolves at most a couple of hops (operand A only) — enough for the common
    "scale an RC channel into a power level" pattern without risking cycles.
    """
    if depth > 3:
        return None
    for nums in logic_lines:
        rule = nums[0]
        if rule != lc_index:
            continue
        op_a_type, op_a_val = nums[4], nums[5]
        if op_a_type == _OPERAND_RC_CHANNEL:
            return op_a_val
        if op_a_type == _OPERAND_LOGIC_CONDITION:
            return _trace_rc_channel(logic_lines, op_a_val, depth + 1)
        return None
    return None


def _normalise_inav(cfg: ParsedConfig) -> VtxConfig:
    table, source = _build_power_table(cfg)
    vtx = VtxConfig(power_table=table, power_table_source=source)
    vtx.low_power_disarm = cfg.settings.get("vtx_low_power_disarm", "OFF").upper()

    candidate_mw: list[int] = []
    global_idx_raw = cfg.settings.get("vtx_power")
    if global_idx_raw is not None:
        try:
            gi = int(global_idx_raw)  # INAV vtx_power is 1-based
        except ValueError:
            gi = 0
        vtx.power_global_index = gi
        gi_mw = _mw_for_index(table, gi)
        vtx.power_global_mw = gi_mw
        if gi_mw is not None:
            candidate_mw.append(gi_mw)

    # Programming framework: logic conditions that set VTX power level.
    for nums in cfg.logic_lines:
        rule, enabled, _activator, operation = nums[0], nums[1], nums[2], nums[3]
        op_a_type, op_a_val = nums[4], nums[5]
        if operation != _INAV_OP_SET_VTX_POWER or not enabled:
            continue

        if op_a_type == _OPERAND_VALUE:
            # Constant power: 0-based operation value -> 1-based table index.
            index = op_a_val + 1
            mw = _mw_for_index(table, index)
            reachable = [mw] if mw is not None else []
            channel = None
            power_index = index
        elif op_a_type == _OPERAND_RC_CHANNEL:
            # Driven directly by an RC channel (a pilot switch/pot): dynamic.
            reachable = _all_selectable_mw(table)
            channel = op_a_val
            power_index = -1
        else:
            # Driven by another logic condition: dynamic; trace back to a channel.
            reachable = _all_selectable_mw(table)
            channel = _trace_rc_channel(cfg.logic_lines, op_a_val)
            power_index = -1

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
    _apply_disarm(vtx, table)
    return vtx
