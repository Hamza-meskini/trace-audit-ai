"""Deterministic Validator for Boolean Features, Ratings, and Protocol Flags."""

import re
from typing import Optional
from app.schemas.contract import RequirementContract
from app.schemas.claim import EvidenceClaim
from app.services.validators import ValidationOutcome


def validate_boolean_flag(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> Optional[ValidationOutcome]:
    """Validate IP ratings, protocol presence, or hardware boolean features."""
    req_text = contract.raw_text.lower()

    # 1. Generalized IP Ingress Protection Check
    ip_match = re.search(r"\b(IP\d{2}[A-Z]?)\b", req_text, re.IGNORECASE)
    if contract.parameter == "ingress_protection" or contract.unit == "IP" or ip_match:
        target_ip = str(contract.expected_value or (ip_match.group(1).upper() if ip_match else "IP67")).upper()
        for claim in claims:
            c_quote_lower = claim.quote.lower()
            if target_ip.lower() in c_quote_lower or "ingress" in c_quote_lower or "immersion" in c_quote_lower:
                if any(k in c_quote_lower for k in ["pass", "zero water", "completed", "verified", "satisfied"]):
                    return ValidationOutcome(
                        status="SUPPORTED",
                        confidence=95.0,
                        reason=f"Ingress protection test confirmed compliance with {target_ip} (zero ingress recorded).",
                        expected_value=target_ip,
                        observed_value=f"PASS {target_ip}",
                    )
                elif any(k in c_quote_lower for k in ["not tested", "missing", "not started", "untested"]):
                    return ValidationOutcome(
                        status="MISSING",
                        confidence=95.0,
                        reason=f"Ingress protection test for {target_ip} is documented as not tested.",
                        expected_value=f"Completed {target_ip} test",
                        observed_value="Not Tested",
                    )
                elif "fail" in c_quote_lower:
                    return ValidationOutcome(
                        status="CONFLICT",
                        confidence=95.0,
                        reason=f"Ingress protection test failed for {target_ip}.",
                        highlight="FAIL",
                        expected_value=target_ip,
                        observed_value="FAIL",
                    )

    # 2. Generalized Boolean Feature / Flag Verification
    if contract.requirement_type in ("boolean", "enumeration") or contract.expected_value is True:
        feature_keywords = [
            w.lower() for w in re.findall(r"\w+", f"{contract.req_code} {contract.title}")
            if len(w) > 3 and w.lower() not in ["shall", "must", "support", "provide", "feature", "enabled", "device", "system", "requirement", "protocol", "rated", "nominal"]
        ]
        if feature_keywords:
            empirical_claims = [
                c for c in claims
                if c.source_authority in ("EMPIRICAL_TEST", "QUALIFICATION_TEST", "VALIDATION_REPORT", "COMPLIANCE_MATRIX")
                and not any(k in c.document_name.lower() for k in ["srs", "product_requirements"])
            ]
            has_verified = any(
                any(kw in c.quote.lower() for kw in feature_keywords) and
                any(p in c.quote.lower() for p in ["pass", "verified", "supported", "implemented", "confirmed", "satisfied", "zero frame errors", "0 errors"])
                for c in empirical_claims
            )
            if has_verified:
                return ValidationOutcome(
                    status="SUPPORTED",
                    confidence=95.0,
                    reason=f"Empirical verification records confirm implementation and compliance for '{contract.title}'.",
                    expected_value=contract.title,
                    observed_value="Verified",
                )

    return None
