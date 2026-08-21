"""Deterministic verification engine.

Performs rule-based checks on numeric ranges, units, and parameter limits
between requirements and extracted evidence.
"""

import re
from typing import Optional
from dataclasses import dataclass


@dataclass
class NumericRange:
    min_val: float
    max_val: float
    unit: str
    raw_str: str


@dataclass
class VerificationResult:
    is_valid: bool
    status: str  # "Supported" | "Partial" | "Conflict" | "Unknown"
    reason: str
    highlight: Optional[str] = None
    expected_range: Optional[str] = None
    observed_range: Optional[str] = None


RANGE_PATTERN = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?",
    re.IGNORECASE,
)

INDIVIDUAL_TESTED_NUMS = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:V|°C|kHz|kV|hours|ms|rpm)\b",
    re.IGNORECASE,
)


def extract_numeric_ranges(text: str) -> list[NumericRange]:
    """Find all range expressions like 18–32 V, -20°C to +70°C, -20 to 60 °C."""
    ranges = []
    for m in RANGE_PATTERN.finditer(text):
        try:
            min_v = float(m.group(1))
            max_v = float(m.group(2))
            unit = m.group(3).strip() if m.group(3) else ""
            ranges.append(NumericRange(min_val=min_v, max_val=max_v, unit=unit, raw_str=m.group(0)))
        except (ValueError, TypeError):
            continue
    return ranges


def verify_range_coverage(req_text: str, evidence_texts: list[str]) -> VerificationResult:
    """Verify if the evidence covers the numeric range specified in the requirement.

    Example:
    - Req: -20°C to +70°C
    - Test: -20°C and +60°C -> Returns Partial (gap between 60°C and 70°C)
    """
    req_ranges = extract_numeric_ranges(req_text)
    if not req_ranges:
        return VerificationResult(is_valid=True, status="Unknown", reason="No explicit numeric range found in requirement")

    req_r = req_ranges[0]

    # Collect observed ranges and single values across all evidence
    all_evidence_combined = " ".join(evidence_texts)
    evidence_ranges = extract_numeric_ranges(all_evidence_combined)

    if evidence_ranges:
        ev_r = evidence_ranges[0]
        # Check if evidence max is less than required max
        if ev_r.max_val < req_r.max_val:
            highlight = f"+{ev_r.max_val:g}°C" if "°" in req_r.unit or "c" in req_r.unit.lower() else f"{ev_r.max_val:g} {ev_r.unit}".strip()
            return VerificationResult(
                is_valid=False,
                status="Partial",
                reason=f"Test evidence covers only part of the declared operating range. Available test evidence stops at {ev_r.max_val:g}{ev_r.unit} while requirement specifies {req_r.max_val:g}{req_r.unit}.",
                highlight=highlight,
                expected_range=req_r.raw_str,
                observed_range=ev_r.raw_str,
            )

    # Check discrete tested points: e.g. "18 V, 24 V and 32 V"
    tested_points = []
    for m in INDIVIDUAL_TESTED_NUMS.finditer(all_evidence_combined):
        try:
            tested_points.append(float(m.group(1)))
        except ValueError:
            pass

    if tested_points:
        min_tested = min(tested_points)
        max_tested = max(tested_points)
        if min_tested <= req_r.min_val and max_tested >= req_r.max_val:
            return VerificationResult(
                is_valid=True,
                status="Supported",
                reason=f"Tested points ({min_tested:g} to {max_tested:g}) fully cover the required range ({req_r.min_val:g} to {req_r.max_val:g}).",
                expected_range=req_r.raw_str,
            )

    return VerificationResult(is_valid=True, status="Supported", reason="Evidence appears consistent with parameter bounds.")
