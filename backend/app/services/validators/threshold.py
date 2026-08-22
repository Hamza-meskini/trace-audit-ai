"""Deterministic Validator for Upper and Lower Bound Thresholds."""

import re
from typing import Optional
from app.schemas.contract import RequirementContract
from app.schemas.claim import EvidenceClaim
from app.services.units import convert_value, are_units_compatible
from app.services.validators import ValidationOutcome


def validate_threshold(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> Optional[ValidationOutcome]:
    """Validate upper (<=) or lower (>=) bound threshold requirements against evidence claims."""
    if contract.operator not in ("<=", "<", ">=", ">"):
        return None

    threshold_val = contract.max_value if contract.operator in ("<=", "<") else contract.min_value
    if threshold_val is None:
        return None

    req_unit = contract.unit
    contract_terms = [w.lower() for w in re.findall(r"\w+", f"{contract.req_code} {contract.title}") if len(w) > 3 and w.lower() not in ["level", "target", "maximum", "minimum", "limit", "rate", "test", "tested", "specification"]]

    # Check threshold and single value claims
    numeric_claims = [
        c for c in claims
        if (c.claim_type in ("threshold", "numeric_range") and (c.value is not None or c.max_value is not None or c.min_value is not None))
    ]

    for claim in numeric_claims:
        if not are_units_compatible(claim.unit, req_unit):
            continue

        if contract_terms and not any(t in claim.quote.lower() for t in contract_terms):
            continue

        raw_val = claim.value if claim.value is not None else (claim.max_value if contract.operator in ("<=", "<") else claim.min_value)
        if raw_val is None:
            continue

        c_val = convert_value(float(raw_val), claim.unit, req_unit)
        if c_val is None:
            continue

        # Upper bound check (e.g. quiescent current <= 150 uA, power <= 45W, contact resistance <= 0.2 mOhm)
        if contract.operator in ("<=", "<"):
            if c_val <= threshold_val:
                return ValidationOutcome(
                    status="SUPPORTED",
                    confidence=95.0,
                    reason=f"Measured value of {c_val:g} {req_unit or ''} satisfies required upper limit of ≤ {threshold_val:g} {req_unit or ''}.",
                    expected_value=f"≤ {threshold_val:g} {req_unit or ''}".strip(),
                    observed_value=f"{c_val:g} {req_unit or ''}".strip(),
                )
            else:
                return ValidationOutcome(
                    status="CONFLICT",
                    confidence=90.0,
                    reason=f"Measured value of {c_val:g} {req_unit or ''} exceeds required maximum limit of ≤ {threshold_val:g} {req_unit or ''}.",
                    highlight=f"{c_val:g} {req_unit or ''}".strip(),
                    expected_value=f"≤ {threshold_val:g} {req_unit or ''}".strip(),
                    observed_value=f"{c_val:g} {req_unit or ''}".strip(),
                )

        # Lower bound check (e.g. dielectric isolation >= 2.5 kV, MTBF >= 250k hrs, energy >= 4.5 J)
        if contract.operator in (">=", ">"):
            if c_val >= threshold_val:
                return ValidationOutcome(
                    status="SUPPORTED",
                    confidence=95.0,
                    reason=f"Demonstrated value of {c_val:g} {req_unit or ''} satisfies required minimum threshold of ≥ {threshold_val:g} {req_unit or ''}.",
                    expected_value=f"≥ {threshold_val:g} {req_unit or ''}".strip(),
                    observed_value=f"{c_val:g} {req_unit or ''}".strip(),
                )
            else:
                return ValidationOutcome(
                    status="CONFLICT",
                    confidence=90.0,
                    reason=f"Demonstrated value of {c_val:g} {req_unit or ''} is below required minimum threshold of ≥ {threshold_val:g} {req_unit or ''}.",
                    highlight=f"{c_val:g} {req_unit or ''}".strip(),
                    expected_value=f"≥ {threshold_val:g} {req_unit or ''}".strip(),
                    observed_value=f"{c_val:g} {req_unit or ''}".strip(),
                )

    return None
