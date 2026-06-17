"""drone-check: document and validate drone flight-controller configuration.

The package connects to a Betaflight / INAV flight controller over USB serial,
reads firmware identity (via MSP) and the full settings (via the CLI ``diff all``
output), normalises the data into a :class:`~drone_check.model.DroneSnapshot`,
and evaluates it against a set of CEL rules.
"""

__version__ = "0.1.0"
