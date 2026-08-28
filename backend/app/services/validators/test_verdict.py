"""Deterministic Validator for Formal Lab Test Verdicts and Compliance Matrix Records."""

from typing import Optional
from app.schemas.contract import RequirementContract
from app.schemas.claim import EvidenceClaim
from app.services.validators import ValidationOutcome


def validate_test_verdict(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> Optional[ValidationOutcome]:
    """Evaluate formal test certificate verdicts and compliance matrix statuses."""
    verdict_claims = [
        c for c in claims
        if c.claim_type == "test_verdict"
        and c.test_result
        and (not c.requirement_id or c.requirement_id.upper() == contract.req_code.upper())
    ]

    if not verdict_claims:
        return None

    # Requirement-local precedence. A PASS in another retrieved candidate must
    # never cancel an authoritative workflow status for this requirement.
    fail_claims = [c for c in verdict_claims if c.test_result == "FAIL"]
    if fail_claims:
        return ValidationOutcome(
            status="CONFLICT",
            confidence=95.0,
            reason="Official laboratory or compliance record reports a failed verification for this requirement.",
            highlight="FAIL",
            expected_value="PASS",
            observed_value="FAIL",
        )

    matrix_claims = [c for c in verdict_claims if c.source_authority == "COMPLIANCE_MATRIX"]
    missing_claims = [c for c in matrix_claims if c.test_result in ("NOT TESTED", "NOT STARTED", "MISSING")]
    if missing_claims:
        return ValidationOutcome(
            status="MISSING",
            confidence=95.0,
            reason="Official compliance matrix record confirms qualification testing has not been performed and required evidence is missing.",
            expected_value="Completed qualification test report",
            observed_value="Test Status: Not Started / Missing Evidence",
        )

    # Check for explicit In-Progress / Partial
    partial_claims = [c for c in matrix_claims if c.test_result in ("IN PROGRESS", "PARTIAL")]
    if partial_claims:
        return ValidationOutcome(
            status="PARTIAL",
            confidence=90.0,
            reason="Test verification records indicate qualification testing is currently in progress or partially completed.",
            expected_value="Completed qualification test report",
            observed_value="Test Status: In Progress",
        )

    return None
