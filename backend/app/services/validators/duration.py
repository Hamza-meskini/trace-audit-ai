"""Deterministic Validator for Timing, Latency, and Duration Constraints."""

import re
from typing import Optional
from app.schemas.contract import RequirementContract
from app.schemas.claim import EvidenceClaim
from app.services.units import convert_value, are_units_compatible
from app.services.validators import ValidationOutcome


def validate_duration(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> Optional[ValidationOutcome]:
    """Validate latency, response timing, or duration requirements."""
    if contract.requirement_type != "duration" and not (contract.unit in ("ms", "us", "µs", "s", "sec", "hours", "h", "min")):
        return None

    threshold_val = contract.max_value if contract.max_value is not None else contract.min_value
    if threshold_val is None:
        return None

    req_unit = contract.unit or "ms"
    contract_terms = [w.lower() for w in re.findall(r"\w+", f"{contract.req_code} {contract.title}") if len(w) > 3 and w.lower() not in ["time", "duration", "latency", "response", "delay", "test", "tested", "specification"]]

    # Find timing claims
    time_claims = [
        c for c in claims
        if (c.claim_type in ("threshold", "numeric_range") and (c.value is not None or c.max_value is not None))
    ]

    for claim in time_claims:
        if not are_units_compatible(claim.unit, req_unit):
            continue

        if contract_terms and not any(t in claim.quote.lower() for t in contract_terms):
            continue

        raw_val = claim.value if claim.value is not None else claim.max_value
        if raw_val is None:
            continue

        c_val = convert_value(float(raw_val), claim.unit, req_unit)
        if c_val is None:
            continue

        # Timing deadline (e.g. latency <= 10.0 ms, pyro-switch <= 5.0 us, precharge <= 200 ms)
        if contract.operator in ("<=", "<", None):
            if c_val <= threshold_val:
                return ValidationOutcome(
                    status="SUPPORTED",
                    confidence=95.0,
                    reason=f"Measured timing latency of {c_val:g} {req_unit} satisfies the required upper limit of ≤ {threshold_val:g} {req_unit}.",
                    expected_value=f"≤ {threshold_val:g} {req_unit}",
                    observed_value=f"{c_val:g} {req_unit}",
                )
            else:
                return ValidationOutcome(
                    status="CONFLICT",
                    confidence=92.0,
                    reason=f"Measured timing latency of {c_val:g} {req_unit} exceeds allowable deadline of ≤ {threshold_val:g} {req_unit}.",
                    highlight=f"{c_val:g} {req_unit}",
                    expected_value=f"≤ {threshold_val:g} {req_unit}",
                    observed_value=f"{c_val:g} {req_unit}",
                )

    return None
