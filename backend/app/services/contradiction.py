"""Cross-document contradiction detection service using structured claims and SI units."""

import re
from typing import Optional
from dataclasses import dataclass
from app.schemas.contract import RequirementContract, RANGE_REGEX
from app.schemas.claim import EvidenceClaim, extract_all_evidence_claims
from app.services.units import are_units_compatible, convert_value, normalize_unit_str


@dataclass
class ContradictionFinding:
    has_conflict: bool
    source_a_doc: str
    source_a_quote: str
    source_b_doc: str
    source_b_quote: str
    highlight: Optional[str]
    description: str


SEMANTIC_CONFLICT_PAIRS = [
    (
        ["credential", "authentication", "login", "password", "authorized", "restricted", "seed-key"],
        ["no login", "no authentication", "unauthenticated", "no password", "open access", "no login required"],
        ["diagnostic", "port", "service", "access", "uds", "security", "calibration", "flashing", "service port"],
        "Discrepancy in access control / authentication requirements across documentation.",
    ),
    (
        ["isolated", "galvanic isolation", "optical and magnetic isolation"],
        ["non-isolated", "common ground", "shared ground"],
        ["ground", "isolation", "barrier", "chassis", "sensing", "dielectric", "return"],
        "Discrepancy in isolation / grounding architecture between specification and technical documentation.",
    ),
]


def detect_contract_contradiction(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
) -> Optional[ContradictionFinding]:
    """Detect if any supplier datasheet or component document directly limits/contradicts the contract bounds."""
    for claim in claims:
        # Only compare against non-specification documents (supplier datasheets, external specs, architecture specs)
        if any(k in claim.document_name.lower() for k in ["srs", "product_requirements"]):
            continue

        # 1. Numeric Range upper/lower limit restriction (e.g. Spec 400-800V vs Datasheet 400-750V max, or Temp +85C vs +70C)
        if contract.requirement_type in ("numeric_range", "threshold") and contract.max_value is not None:
            if claim.claim_type == "numeric_range" and claim.max_value is not None:
                if are_units_compatible(claim.unit, contract.unit):
                    c_claim_max = convert_value(claim.max_value, claim.unit, contract.unit)
                    if c_claim_max is not None and c_claim_max < contract.max_value - 0.5:
                        contract_terms = [w.lower() for w in re.findall(r"\w+", f"{contract.req_code} {contract.title}") if len(w) > 3 and w.lower() not in ["operating", "temperature", "voltage", "ambient", "system", "continuous"]]
                        claim_param = (claim.parameter or "").lower()
                        if not contract_terms or any(t in claim.quote.lower() for t in contract_terms) or any(t in claim_param for t in contract_terms):
                            is_datasheet = any(k in claim.document_name.lower() for k in ["datasheet", "ds-", "oem", "supplier", "component", "spec"])
                            if is_datasheet:
                                highlight = f"{claim.max_value:g} {claim.unit or ''}".strip()
                                desc = (
                                    f"Direct parameter discrepancy identified between {contract.title} and {claim.document_name}. "
                                    f"Specification mandates operation up to {contract.max_value:g} {contract.unit or ''}, but {claim.document_name} restricts maximum rated operation to {c_claim_max:g} {contract.unit or ''}."
                                )
                                return ContradictionFinding(
                                    has_conflict=True,
                                    source_a_doc="Product Specification",
                                    source_a_quote=contract.raw_text[:200],
                                    source_b_doc=claim.document_name,
                                    source_b_quote=claim.quote,
                                    highlight=highlight,
                                    description=desc,
                                )

        # 2. Semantic discrepancy against contract
        for set_a, set_b, topics, explanation in SEMANTIC_CONFLICT_PAIRS:
            # Check if contract matches set_a and claim matches set_b
            if any(t in contract.raw_text.lower() for t in topics) and any(t in claim.quote.lower() for t in topics):
                a_matches = any(kw in contract.raw_text.lower() for kw in set_a)
                b_matches = any(kw in claim.quote.lower() for kw in set_b)
                if a_matches and b_matches:
                    neg_kw = next((kw for kw in set_b if kw in claim.quote.lower()), None)
                    return ContradictionFinding(
                        has_conflict=True,
                        source_a_doc="Product Specification",
                        source_a_quote=contract.raw_text[:200],
                        source_b_doc=claim.document_name,
                        source_b_quote=claim.quote,
                        highlight=neg_kw,
                        description=f"{explanation} Specification requires compliance while {claim.document_name} states '{neg_kw}'.",
                    )

    return None


def detect_cross_document_contradiction(
    evidence_items: list[dict],
    contract: Optional[RequirementContract] = None,
) -> Optional[ContradictionFinding]:
    """Compare evidence chunks and requirement contract to identify value or semantic contradictions."""
    claims = extract_all_evidence_claims(evidence_items, contract)

    # 1. Check against requirement contract first
    if contract:
        contract_finding = detect_contract_contradiction(contract, claims)
        if contract_finding:
            return contract_finding

    # 2. Compare semantic contradiction pairs across documents (topic-gated)
    if len(claims) < 2:
        return None

    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            claim_a = claims[i]
            claim_b = claims[j]

            if claim_a.document_name == claim_b.document_name:
                continue

            # Compare semantic pairs (e.g. access control, isolation vs shared ground)
            for set_a, set_b, topics, explanation in SEMANTIC_CONFLICT_PAIRS:
                topic_match = any(t in claim_a.quote.lower() for t in topics) and any(t in claim_b.quote.lower() for t in topics)
                if not topic_match:
                    continue

                if any(kw in claim_a.quote.lower() for kw in set_a) and any(kw in claim_b.quote.lower() for kw in set_b):
                    neg_kw = next((kw for kw in set_b if kw in claim_b.quote.lower()), None)
                    return ContradictionFinding(
                        has_conflict=True,
                        source_a_doc=claim_a.document_name,
                        source_a_quote=claim_a.quote,
                        source_b_doc=claim_b.document_name,
                        source_b_quote=claim_b.quote,
                        highlight=neg_kw,
                        description=f"{explanation} One document requires credentials while {claim_b.document_name} states '{neg_kw}'.",
                    )
                if any(kw in claim_b.quote.lower() for kw in set_a) and any(kw in claim_a.quote.lower() for kw in set_b):
                    neg_kw = next((kw for kw in set_b if kw in claim_a.quote.lower()), None)
                    return ContradictionFinding(
                        has_conflict=True,
                        source_a_doc=claim_b.document_name,
                        source_a_quote=claim_b.quote,
                        source_b_doc=claim_a.document_name,
                        source_b_quote=claim_a.quote,
                        highlight=neg_kw,
                        description=f"{explanation} One document requires credentials while {claim_a.document_name} states '{neg_kw}'.",
                    )

    return None
