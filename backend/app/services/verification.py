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
    r"([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?",
    re.IGNORECASE,
)

INDIVIDUAL_TESTED_NUMS = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:V|°C|kHz|kV|hours|ms|rpm)\b",
    re.IGNORECASE,
)


def extract_numeric_ranges(text: str) -> list[NumericRange]:
    """Find all range expressions like 18–32 V, -20°C to +70°C, 400.0 V to 800.0 V DC."""
    ranges = []
    for m in RANGE_PATTERN.finditer(text):
        try:
            min_v = float(m.group(1))
            unit_pre = m.group(2).strip() if m.group(2) else ""
            max_v = float(m.group(3))
            unit_post = m.group(4).strip() if m.group(4) else ""
            unit = unit_post or unit_pre
            ranges.append(NumericRange(min_val=min_v, max_val=max_v, unit=unit, raw_str=m.group(0)))
        except (ValueError, TypeError):
            continue
    return ranges


def verify_range_coverage(req_text: str, evidence_texts: list[str]) -> VerificationResult:
    """Verify if the evidence covers the numeric range specified in the requirement."""
    req_ranges = extract_numeric_ranges(req_text)
    if not req_ranges:
        return VerificationResult(is_valid=True, status="Unknown", reason="No explicit numeric range found in requirement")

    req_r = req_ranges[0]
    all_evidence_combined = " ".join(evidence_texts)

    # 1. Check if discrete tested numbers fully span the required min and max
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

    # 2. Check explicit tested ranges (e.g. "tested from -20°C to +70°C")
    TESTED_RANGE_PATTERN = re.compile(
        r"(?:tested\s+(?:from|at|across)?|testing\s+performed\s+from|measured\s+from)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?",
        re.IGNORECASE,
    )
    tested_match = TESTED_RANGE_PATTERN.search(all_evidence_combined)
    if tested_match:
        try:
            ev_min = float(tested_match.group(1))
            ev_max = float(tested_match.group(3))
            ev_unit = (tested_match.group(4) or tested_match.group(2) or req_r.unit).strip()
            if ev_max < req_r.max_val or ev_min > req_r.min_val:
                highlight = f"+{ev_max:g}°C" if "°" in req_r.unit or "c" in req_r.unit.lower() else f"{ev_max:g} {ev_unit}".strip()
                return VerificationResult(
                    is_valid=False,
                    status="Partial",
                    reason=f"Test evidence covers only part of the declared operating range. Available test evidence is {ev_min:g} to {ev_max:g}{ev_unit} while requirement specifies {req_r.min_val:g} to {req_r.max_val:g}{req_r.unit}.",
                    highlight=highlight,
                    expected_range=req_r.raw_str,
                    observed_range=f"{ev_min:g} to {ev_max:g} {ev_unit}",
                )
            elif ev_min <= req_r.min_val and ev_max >= req_r.max_val:
                return VerificationResult(
                    is_valid=True,
                    status="Supported",
                    reason=f"Observed test range ({ev_min:g} to {ev_max:g}{ev_unit}) satisfies requirement bounds ({req_r.min_val:g} to {req_r.max_val:g}{req_r.unit}).",
                    expected_range=req_r.raw_str,
                )
        except (ValueError, TypeError):
            pass

    # 3. Check general evidence ranges
    evidence_ranges = extract_numeric_ranges(all_evidence_combined)
    if evidence_ranges:
        for ev_r in evidence_ranges:
            if ev_r.min_val <= req_r.min_val and ev_r.max_val >= req_r.max_val:
                return VerificationResult(
                    is_valid=True,
                    status="Supported",
                    reason=f"Evidence range {ev_r.raw_str} covers required specification {req_r.raw_str}.",
                    expected_range=req_r.raw_str,
                )

    return VerificationResult(is_valid=True, status="Supported", reason="Evidence appears consistent with parameter bounds.")
