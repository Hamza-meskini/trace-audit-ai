"""Physical unit normalization and exact SI conversion matrix.

Avoids fragile substring matching and guarantees safe conversions across:
  - Voltage: mV, V, kV
  - Current: uA / µA, mA, A, kA
  - Time / Duration: ns, us / µs, ms, s, min, h / hours
  - Frequency / Baud Rate: Hz, kHz, MHz, GHz, Mbps
  - Pressure: Pa, kPa, mbar, bar
  - Energy: mJ, J, kJ
  - Resistance: µOhm / uOhm, mOhm, Ohm, kOhm
  - Temperature: °C
"""

from typing import Optional, Union


# Base unit categories and conversion factors to SI base
UNIT_CONVERSION_TABLE = {
    # Voltage (Base: V)
    "mv": ("voltage", 1e-3, "v"),
    "v": ("voltage", 1.0, "v"),
    "vdc": ("voltage", 1.0, "v"),
    "v dc": ("voltage", 1.0, "v"),
    "vac": ("voltage", 1.0, "v"),
    "v ac": ("voltage", 1.0, "v"),
    "kv": ("voltage", 1e3, "v"),
    "kv ac": ("voltage", 1e3, "v"),

    # Current (Base: A)
    "na": ("current", 1e-9, "a"),
    "ua": ("current", 1e-6, "a"),
    "µa": ("current", 1e-6, "a"),
    "ma": ("current", 1e-3, "a"),
    "a": ("current", 1.0, "a"),
    "adc": ("current", 1.0, "a"),
    "ka": ("current", 1e3, "a"),

    # Time / Duration (Base: s)
    "ns": ("time", 1e-9, "s"),
    "us": ("time", 1e-6, "s"),
    "µs": ("time", 1e-6, "s"),
    "microseconds": ("time", 1e-6, "s"),
    "ms": ("time", 1e-3, "s"),
    "milliseconds": ("time", 1e-3, "s"),
    "s": ("time", 1.0, "s"),
    "sec": ("time", 1.0, "s"),
    "seconds": ("time", 1.0, "s"),
    "min": ("time", 60.0, "s"),
    "minutes": ("time", 60.0, "s"),
    "h": ("time", 3600.0, "s"),
    "hr": ("time", 3600.0, "s"),
    "hrs": ("time", 3600.0, "s"),
    "hours": ("time", 3600.0, "s"),

    # Frequency & Data rate (Base: Hz / bps)
    "hz": ("frequency", 1.0, "hz"),
    "khz": ("frequency", 1e3, "hz"),
    "mhz": ("frequency", 1e6, "hz"),
    "ghz": ("frequency", 1e9, "hz"),
    "mbps": ("data_rate", 1e6, "bps"),
    "kbps": ("data_rate", 1e3, "bps"),

    # Pressure (Base: Pa)
    "pa": ("pressure", 1.0, "pa"),
    "kpa": ("pressure", 1e3, "pa"),
    "mbar": ("pressure", 100.0, "pa"),
    "bar": ("pressure", 1e5, "pa"),

    # Energy (Base: J)
    "mj": ("energy", 1e-3, "j"),
    "j": ("energy", 1.0, "j"),
    "kj": ("energy", 1e3, "j"),

    # Resistance (Base: Ohm)
    "uo": ("resistance", 1e-6, "ohm"),
    "uohm": ("resistance", 1e-6, "ohm"),
    "µohm": ("resistance", 1e-6, "ohm"),
    "mo": ("resistance", 1e-3, "ohm"),
    "mohm": ("resistance", 1e-3, "ohm"),
    "ohm": ("resistance", 1.0, "ohm"),
    "kohm": ("resistance", 1e3, "ohm"),

    # Temperature (Base: °C)
    "°c": ("temperature", 1.0, "°c"),
    "c": ("temperature", 1.0, "°c"),
    "degc": ("temperature", 1.0, "°c"),

    # Ingress Protection
    "ip": ("ingress", 1.0, "ip"),

    # Ratio
    "%": ("percentage", 1.0, "%"),
    "percent": ("percentage", 1.0, "%"),
    "db": ("decibel", 1.0, "db"),
}


def normalize_unit_str(unit_str: Optional[str]) -> str:
    """Normalize unit string for exact dictionary lookup."""
    if not unit_str:
        return ""
    cleaned = unit_str.strip().lower()
    cleaned = cleaned.replace("°c", "c").replace("degrees c", "c").replace("µ", "u")
    if cleaned in ("c", "°c"):
        return "c"
    # Remove trailing dots / brackets
    cleaned = cleaned.strip(".()[]")
    return cleaned


def are_units_compatible(unit_a: Optional[str], unit_b: Optional[str]) -> bool:
    """Check if two units represent the same physical quantity."""
    ua = normalize_unit_str(unit_a)
    ub = normalize_unit_str(unit_b)
    if not ua or not ub:
        return False  # Do not assume compatibility if either unit is missing

    if ua == ub:
        return True

    info_a = UNIT_CONVERSION_TABLE.get(ua)
    info_b = UNIT_CONVERSION_TABLE.get(ub)

    if info_a and info_b:
        return info_a[0] == info_b[0]  # Check matching dimension

    return False


def convert_value(
    value: float,
    from_unit: Optional[str],
    to_unit: Optional[str],
) -> Optional[float]:
    """Convert value from from_unit to to_unit using exact SI multipliers."""
    if not from_unit or not to_unit:
        return None  # Cannot safely convert without explicit units

    ua = normalize_unit_str(from_unit)
    ub = normalize_unit_str(to_unit)

    if ua == ub:
        return value

    info_a = UNIT_CONVERSION_TABLE.get(ua)
    info_b = UNIT_CONVERSION_TABLE.get(ub)

    if not info_a or not info_b:
        return None  # Unknown unit conversion

    dim_a, mult_a, _ = info_a
    dim_b, mult_b, _ = info_b

    if dim_a != dim_b:
        return None  # Incompatible physical dimensions (e.g. V vs A)

    # Convert to SI base then to target unit
    value_in_base = value * mult_a
    value_in_target = value_in_base / mult_b
    return value_in_target
