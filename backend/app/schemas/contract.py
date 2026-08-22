"""Structured Requirement Contract schema for formal auditing and validation."""

from typing import Optional, Union, Literal
from pydantic import BaseModel, Field
import re


RequirementType = Literal[
    "numeric_range",
    "threshold",
    "boolean",
    "duration",
    "test_result",
    "enumeration",
    "semantic",
    "other",
]


class RequirementContract(BaseModel):
    """Structured engineering requirement contract."""

    requirement_id: str
    req_code: str
    title: str
    description: Optional[str] = None
    category: str = "General"
    requirement_type: RequirementType = "semantic"
    subject: Optional[str] = None
    parameter: Optional[str] = None
    operator: Optional[str] = None  # "between", "<=", "<", ">=", ">", "==", "in", "not_in"
    expected_value: Optional[Union[float, str, bool]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    tolerance: Optional[float] = None
    unit: Optional[str] = None
    conditions: list[str] = Field(default_factory=list)
    verification_method: Optional[str] = None  # "test", "calculation", "simulation", "inspection"
    mandatory: bool = True
    raw_text: str = ""


# Regex helpers for deterministic contract parsing
RANGE_REGEX = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ]+(?:\s+[a-zA-Z]+)?)?\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ]+(?:\s+[a-zA-Z]+)?)?",
    re.IGNORECASE,
)

THRESHOLD_LE_REGEX = re.compile(
    r"(?:<=|≤|not\s+exceed|maximum\s+of|max(?:imum)?\s*[:=]?|limited\s+to\s*≤?|within\s+(?:maximum\s+)?latency\s*≤?|within\s*≤?|less\s+than\s+or\s+equal\s+to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?",
    re.IGNORECASE,
)

THRESHOLD_GE_REGEX = re.compile(
    r"(?:>=|≥|minimum\s+of|min(?:imum)?\s*[:=]?|at\s+least|exceed|greater\s+than\s+or\s+equal\s+to|store\s+minimum)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?",
    re.IGNORECASE,
)

TOLERANCE_REGEX = re.compile(
    r"[±\+\/\-]+\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?",
    re.IGNORECASE,
)

IP_REGEX = re.compile(r"\b(IP\d{2}[A-Z]?)\b", re.IGNORECASE)


def parse_requirement_contract(
    req_code: str,
    title: str,
    description: Optional[str] = None,
    category: str = "General",
) -> RequirementContract:
    """Build a structured RequirementContract from requirement text deterministically.
    
    If the requirement is not confidently numeric/threshold, it preserves the 
    semantic type without inventing values.
    """
    full_text = f"{title}. {description or ''}".strip()
    contract = RequirementContract(
        requirement_id=req_code,
        req_code=req_code,
        title=title,
        description=description,
        category=category,
        requirement_type="semantic",
        raw_text=full_text,
    )

    # 1. Check for IP rating
    ip_match = IP_REGEX.search(full_text)
    if ip_match and ("ingress" in full_text.lower() or "enclosure" in full_text.lower() or "protection" in full_text.lower()):
        contract.requirement_type = "enumeration"
        contract.parameter = "ingress_protection"
        contract.operator = "=="
        contract.expected_value = ip_match.group(1).upper()
        contract.unit = "IP"
        return contract

    # 2. Check for numeric ranges: e.g. "400.0 V DC to 800.0 V DC", "-40°C to +85°C", "18–30 V"
    range_match = RANGE_REGEX.search(full_text)
    if range_match:
        try:
            min_v = float(range_match.group(1))
            unit_pre = range_match.group(2)
            max_v = float(range_match.group(3))
            unit_post = range_match.group(4)
            unit = (unit_post or unit_pre or "").strip()

            contract.requirement_type = "numeric_range"
            contract.operator = "between"
            contract.min_value = min_v
            contract.max_value = max_v
            contract.unit = unit or None
            
            # Extract tolerance if present
            tol_match = TOLERANCE_REGEX.search(full_text)
            if tol_match:
                contract.tolerance = float(tol_match.group(1))
            
            return contract
        except (ValueError, TypeError):
            pass

    # 3. Check for duration / latency threshold (<= X ms, <= X us, <= X s, <= X hours)
    if any(tw in full_text.lower() for tw in ["latency", "time", "duration", "response", "delay", "disconnect", "cycle", "hours"]):
        # Check <= threshold
        le_m = THRESHOLD_LE_REGEX.search(full_text)
        if le_m:
            try:
                val = float(le_m.group(1))
                unit = le_m.group(2).strip() if le_m.group(2) else ""
                if unit.lower() in ("ms", "us", "µs", "s", "sec", "seconds", "hours", "h", "min", "minutes"):
                    contract.requirement_type = "duration"
                    contract.operator = "<="
                    contract.max_value = val
                    contract.unit = unit
                    return contract
            except (ValueError, TypeError):
                pass

    # 4. Check for general <= threshold (e.g. power <= 45W, current <= 150 uA, error <= 0.5%)
    le_m = THRESHOLD_LE_REGEX.search(full_text)
    if le_m:
        try:
            val = float(le_m.group(1))
            unit = le_m.group(2).strip() if le_m.group(2) else ""
            contract.requirement_type = "threshold"
            contract.operator = "<="
            contract.max_value = val
            contract.unit = unit or None
            return contract
        except (ValueError, TypeError):
            pass

    # 5. Check for general >= threshold (e.g. dielectric >= 2.5 kV, MTBF >= 250,000 hours, energy >= 4.5 J)
    ge_m = THRESHOLD_GE_REGEX.search(full_text)
    if ge_m:
        try:
            val = float(ge_m.group(1))
            unit = ge_m.group(2).strip() if ge_m.group(2) else ""
            contract.requirement_type = "threshold"
            contract.operator = ">="
            contract.min_value = val
            contract.unit = unit or None
            return contract
        except (ValueError, TypeError):
            pass

    # 6. Check for boolean flags (e.g. secure boot, galvanic isolation, authentication)
    if any(kw in full_text.lower() for kw in ["secure boot", "hardware root-of-trust", "galvanic isolation", "ecdsa", "authentication"]):
        contract.requirement_type = "boolean"
        contract.operator = "=="
        contract.expected_value = True
        return contract

    return contract
