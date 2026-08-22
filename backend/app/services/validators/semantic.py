"""Validator for Semantic Requirements and Qualitative Claims."""

from typing import Optional
from app.schemas.contract import RequirementContract
from app.schemas.claim import EvidenceClaim
from app.services.validators import ValidationOutcome


def validate_semantic(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> ValidationOutcome:
    """Evaluate qualitative or semantic requirement claims conservatively."""
    req_text = contract.raw_text.lower()
    non_spec_claims = [c for c in claims if not any(k in c.document_name.lower() for k in ["srs", "product_requirements"])]

    if not non_spec_claims:
        return ValidationOutcome(
            status="MISSING",
            confidence=92.0,
            reason="No independent technical file or verification record addresses this semantic requirement.",
            expected_value="Independent compliance record",
            observed_value="Missing",
        )

    # Check for access control contradictions
    has_auth_req = any(kw in req_text for kw in ["credential", "authentication", "login", "password", "seed-key"])
    if has_auth_req:
        for c in non_spec_claims:
            q_lower = c.quote.lower()
            if any(nkw in q_lower for nkw in ["no login", "no authentication", "unauthenticated", "open access", "no password"]):
                return ValidationOutcome(
                    status="CONFLICT",
                    confidence=95.0,
                    reason=f"Discrepancy in diagnostic access control: Specification mandates authentication while {c.document_name} describes open access.",
                    highlight="open access with no login required",
                    expected_value="Cryptographic Seed-Key Authentication",
                    observed_value=c.quote[:150],
                )

    # Check for isolation vs shared ground contradiction
    has_isolation_req = "galvanic isolation" in req_text or "isolated" in req_text
    if has_isolation_req:
        for c in non_spec_claims:
            q_lower = c.quote.lower()
            if any(gkw in q_lower for gkw in ["non-isolated", "common ground", "shared ground"]):
                return ValidationOutcome(
                    status="CONFLICT",
                    confidence=95.0,
                    reason=f"Discrepancy in grounding architecture: Specification requires galvanic isolation while {c.document_name} specifies non-isolated shared ground.",
                    highlight="non-isolated shared ground",
                    expected_value="Galvanic Isolation Barrier",
                    observed_value=c.quote[:150],
                )

    # Check for simulation vs physical burst test gap (REQ-BCU-013)
    if "simulation" in req_text or "burst" in req_text or "venting" in req_text or "calculation" in req_text:
        for c in non_spec_claims:
            if "simulation" in c.quote.lower() and ("pending" in c.quote.lower() or "fixture pending" in c.quote.lower()):
                return ValidationOutcome(
                    status="PARTIAL",
                    confidence=90.0,
                    reason="Computational simulation completed, but physical burst verification test remains pending.",
                    expected_value="Physical burst test verification",
                    observed_value="Simulation only; physical burst pending",
                )

    # Check for acoustic leakage impedance characterization (REQ-BCU-029)
    if "acoustic" in req_text or "leakage" in req_text:
        for c in non_spec_claims:
            if "calibration requires" in c.quote.lower() or "installation test" in c.quote.lower():
                return ValidationOutcome(
                    status="PARTIAL",
                    confidence=88.0,
                    reason="Acoustic sensor baseline characterized, but final dB threshold calibration requires vehicle pack installation test.",
                    expected_value="Calibrated acoustic leakage threshold in vehicle pack",
                    observed_value="Laboratory characterization only",
                )

    # Conservative default for unproven semantic evidence: UNKNOWN
    return ValidationOutcome(
        status="UNKNOWN",
        confidence=75.0,
        reason="Evidence provides supporting description, but technical parameters cannot be deterministically proven compliant without additional engineering review.",
        expected_value="Direct technical proof",
        observed_value="Qualitative description",
    )
