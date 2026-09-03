"""Standards-based physical-unit parsing and deterministic conversion.

Semantic interpretation belongs to the LLM. This module has one narrower
responsibility: determine whether two recognized units have the same
dimensionality and, when they do, perform reproducible arithmetic with Pint.
It deliberately has no project- or benchmark-specific alias table.
"""

from __future__ import annotations

from enum import Enum
import re
from functools import lru_cache
from typing import Optional, Any

import pint
from pint.errors import DimensionalityError, OffsetUnitCalculusError, PintError


class UnitCompatibility(str, Enum):
    """Tri-state result so unknown spelling is not called a contradiction."""

    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


def _registry(*, case_sensitive: bool) -> pint.UnitRegistry:
    registry = pint.UnitRegistry(case_sensitive=case_sensitive)
    # AC/DC/RMS describe measurement modality, not another physical
    # dimension. This parses conventional forms such as "V DC" while
    # preserving voltage dimensionality.
    for definition in ("AC = []", "DC = []", "RMS = []"):
        try:
            registry.define(definition)
        except Exception:
            pass
    return registry


_STRICT_REGISTRY = _registry(case_sensitive=True)
_WORD_REGISTRY = _registry(case_sensitive=False)


def _clean_unit_text(unit_text: Optional[str]) -> str:
    if not unit_text:
        return ""
    cleaned = str(unit_text).strip().strip(".()[]")
    if not cleaned:
        return ""

    # Typography repair, not semantic aliasing.
    cleaned = cleaned.replace("μ", "µ")
    if cleaned.startswith("�") and len(cleaned) > 1:
        cleaned = "µ" + cleaned[1:]
    cleaned = re.sub(r"(?i)\b(?:degrees?|deg)\s*([CFK])\b", r"°\1", cleaned)
    # Split compact electrical modality suffixes: VDC -> V DC, kVAC -> kV AC.
    cleaned = re.sub(r"(?i)\b([fpnumkMGT]?V)(AC|DC|RMS)\b", r"\1 \2", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


@lru_cache(maxsize=512)
def _canonical_unit(unit_text: str) -> str | None:
    """Resolve spelling/symbol variants to a Pint canonical expression."""
    cleaned = _clean_unit_text(unit_text)
    if not cleaned:
        return None

    # Preserve case-sensitive SI prefixes/symbols first (µs, mV, MV, dB).
    # Fall back to case-insensitive parsing for prose (Volts, Ohms).
    for registry in (_STRICT_REGISTRY, _WORD_REGISTRY):
        try:
            parsed = registry.parse_units(cleaned)
            canonical = str(parsed)
            # Reparse in one registry so comparison/conversion never mixes
            # objects owned by different registries.
            _STRICT_REGISTRY.parse_units(canonical)
            return canonical
        except (PintError, ValueError, TypeError, AssertionError):
            continue
    return None


def normalize_unit_str(unit_str: Optional[str]) -> str:
    """Return a stable compact representation, or empty when unrecognized."""
    canonical = _canonical_unit(_clean_unit_text(unit_str))
    if canonical is None:
        return ""
    try:
        compact = format(_STRICT_REGISTRY.parse_units(canonical), "~")
    except (PintError, ValueError, TypeError, AssertionError):
        return ""
    normalized = (
        compact.replace("µ", "u")
        .replace("μ", "u")
        .replace("Ω", "ohm")
        .replace(" ", "")
        .lower()
    )
    return re.sub(r"^(?:(?:ac|dc|rms)\*)+", "", normalized)


def _dimensionality(canonical: str) -> Any:
    return _STRICT_REGISTRY.parse_units(canonical).dimensionality


def unit_compatibility(
    unit_a: Optional[str],
    unit_b: Optional[str],
) -> UnitCompatibility:
    """Compare recognized physical dimensions without guessing unknown units."""
    canonical_a = _canonical_unit(_clean_unit_text(unit_a))
    canonical_b = _canonical_unit(_clean_unit_text(unit_b))
    if canonical_a is None or canonical_b is None:
        return UnitCompatibility.UNKNOWN
    try:
        if _dimensionality(canonical_a) == _dimensionality(canonical_b):
            return UnitCompatibility.COMPATIBLE
        return UnitCompatibility.INCOMPATIBLE
    except (PintError, ValueError, TypeError, AssertionError):
        return UnitCompatibility.UNKNOWN


def are_units_compatible(unit_a: Optional[str], unit_b: Optional[str]) -> bool:
    """Backward-compatible boolean API for deterministic numeric validators."""
    return unit_compatibility(unit_a, unit_b) == UnitCompatibility.COMPATIBLE


def convert_value(
    value: float,
    from_unit: Optional[str],
    to_unit: Optional[str],
) -> Optional[float]:
    """Convert a value using Pint; return ``None`` when conversion is unsafe."""
    canonical_from = _canonical_unit(_clean_unit_text(from_unit))
    canonical_to = _canonical_unit(_clean_unit_text(to_unit))
    if canonical_from is None or canonical_to is None:
        return None
    if unit_compatibility(from_unit, to_unit) != UnitCompatibility.COMPATIBLE:
        return None
    try:
        source = _STRICT_REGISTRY.Quantity(float(value), canonical_from)
        converted = source.to(canonical_to)
        return float(converted.magnitude)
    except (DimensionalityError, OffsetUnitCalculusError, PintError, ValueError, TypeError, AssertionError):
        return None
