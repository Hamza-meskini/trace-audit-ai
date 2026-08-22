"""Deterministic Validator for Numeric Operating Ranges."""

import re
from typing import Optional
from app.schemas.contract import RequirementContract
from app.schemas.claim import EvidenceClaim
from app.services.units import convert_value, are_units_compatible
from app.services.validators import ValidationOutcome


def validate_numeric_range(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> Optional[ValidationOutcome]:
    """Validate a numeric range requirement against extracted evidence claims."""
    if contract.min_value is None or contract.max_value is None:
        return None

    req_min = contract.min_value
    req_max = contract.max_value
    req_unit = contract.unit

    contract_terms = [w.lower() for w in re.findall(r"\w+", f"{contract.req_code} {contract.title}") if len(w) > 3 and w.lower() not in ["operating", "range", "nominal", "extended", "profile", "test", "tested", "specification", "requirements"]]

    # 1. Check discrete point sweeps first (e.g. tested at 400V, 600V, 800V)
    for claim in claims:
        if claim.claim_type == "discrete_sweep" and claim.discrete_points:
            if are_units_compatible(claim.unit, req_unit):
                # Topic relevance check
                if contract_terms and not any(t in claim.quote.lower() for t in contract_terms):
                    continue

                converted_pts = []
                for pt in claim.discrete_points:
                    c_pt = convert_value(pt, claim.unit, req_unit)
                    if c_pt is not None:
                        converted_pts.append(c_pt)

                if converted_pts:
                    min_tested = min(converted_pts)
                    max_tested = max(converted_pts)
                    if min_tested <= req_min and max_tested >= req_max:
                        return ValidationOutcome(
                            status="SUPPORTED",
                            confidence=95.0,
                            reason=f"Tested points ({min_tested:g} to {max_tested:g} {req_unit or ''}) fully span required range ({req_min:g} to {req_max:g} {req_unit or ''}).",
                            expected_value=f"{req_min:g} to {req_max:g} {req_unit or ''}".strip(),
                            observed_value=f"Points: {', '.join(f'{p:g}' for p in converted_pts)}",
                        )

    # 2. Check numeric range claims (e.g. tested from -20°C to +70°C)
    range_claims = [c for c in claims if c.claim_type == "numeric_range" and c.min_value is not None and c.max_value is not None]
    
    for claim in range_claims:
        if not are_units_compatible(claim.unit, req_unit):
            continue

        if contract_terms and not any(t in claim.quote.lower() for t in contract_terms):
            continue

        c_min = convert_value(claim.min_value, claim.unit, req_unit)
        c_max = convert_value(claim.max_value, claim.unit, req_unit)

        if c_min is None or c_max is None:
            continue

        # Check for full coverage
        if c_min <= req_min and c_max >= req_max:
            return ValidationOutcome(
                status="SUPPORTED",
                confidence=95.0,
                reason=f"Evidence range ({c_min:g} to {c_max:g} {req_unit or ''}) fully covers required bounds ({req_min:g} to {req_max:g} {req_unit or ''}).",
                expected_value=f"{req_min:g} to {req_max:g} {req_unit or ''}".strip(),
                observed_value=f"{c_min:g} to {c_max:g} {req_unit or ''}".strip(),
            )

        # Check for partial coverage (gap at min or max)
        if (c_min > req_min and c_min < req_max) or (c_max < req_max and c_max > req_min) or (c_min >= req_min and c_max <= req_max):
            highlight = f"+{c_max:g}°C" if "c" in (req_unit or "").lower() else f"{c_max:g} {req_unit or ''}".strip()
            return ValidationOutcome(
                status="PARTIAL",
                confidence=90.0,
                reason=f"Test evidence covers only part of the declared operating range: tested from {c_min:g} to {c_max:g} {req_unit or ''}, leaving a verification gap against required bounds ({req_min:g} to {req_max:g} {req_unit or ''}).",
                highlight=highlight,
                expected_value=f"{req_min:g} to {req_max:g} {req_unit or ''}".strip(),
                observed_value=f"{c_min:g} to {c_max:g} {req_unit or ''}".strip(),
            )

        # Check for complete mismatch / conflict (e.g. 10V-15V vs required 18V-32V)
        if c_max < req_min or c_min > req_max:
            return ValidationOutcome(
                status="CONFLICT",
                confidence=92.0,
                reason=f"Evidence operating bounds ({c_min:g} to {c_max:g} {req_unit or ''}) fall completely outside the required envelope ({req_min:g} to {req_max:g} {req_unit or ''}).",
                highlight=f"{c_max:g} {req_unit or ''}".strip(),
                expected_value=f"{req_min:g} to {req_max:g} {req_unit or ''}".strip(),
                observed_value=f"{c_min:g} to {c_max:g} {req_unit or ''}".strip(),
            )

    return None
