from drone_check.bfcd.commands import (
    BY_CODE,
    BY_NAME,
    COMMANDS,
    Handling,
    Priority,
    commands_for_priority,
)


def test_codes_are_unique():
    codes = [c.code for c in COMMANDS]
    assert len(codes) == len(set(codes)), "duplicate MSP command code in the matrix"


def test_names_are_unique_and_well_formed():
    assert len(BY_NAME) == len(COMMANDS)
    assert all(c.name.startswith("MSP_") for c in COMMANDS)


def test_by_code_lookup():
    assert BY_CODE[1].name == "MSP_API_VERSION"
    assert BY_NAME["MSP_VTX_CONFIG"].code == 88


def test_priority_a_excludes_blocked_writes():
    a = commands_for_priority(Priority.A)
    names = {c.name for c in a}
    assert "MSP_API_VERSION" in names
    # MSP_EEPROM_WRITE / MSP_REBOOT are priority A but blocked -> excluded.
    assert "MSP_EEPROM_WRITE" not in names
    assert all(c.handling is not Handling.BLOCKED for c in a)


def test_every_command_has_a_handling():
    assert all(isinstance(c.handling, Handling) for c in COMMANDS)
