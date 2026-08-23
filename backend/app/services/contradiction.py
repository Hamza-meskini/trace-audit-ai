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
    """Detect if any supplier datasheet or component document directly limits/contradicts the contract bounds.
    
    Enforces entity and scope awareness: a component datasheet limit (e.g. ASIC max 750V)
    does not create a conflict for a system-level requirement (e.g. BCU Pack 400-800V)
    unless the requirement is specifically scoped to that component.
    """
    contract_title_lower = contract.title.lower()
    contract_raw_lower = contract.raw_text.lower()
    contract_scope = (contract.scope or "System").lower()

    # Check if there is already empirical test evidence proving the system envelope
    has_system_empirical_support = any(
        c.claim_type in ("numeric_range", "discrete_sweep") and
        c.source_authority in ("EMPIRICAL_TEST", "QUALIFICATION_TEST", "VALIDATION_REPORT") and
        (c.min_value is not None and c.max_value is not None and contract.min_value is not None and contract.max_value is not None and
         c.min_value <= contract.min_value and c.max_value >= contract.max_value)
        for c in claims
    )

    for claim in claims:
        # Only compare against non-specification documents (supplier datasheets, external specs, architecture specs)
        if any(k in claim.document_name.lower() for k in ["srs", "product_requirements"]):
            continue

        claim_doc_lower = claim.document_name.lower()
        is_datasheet = any(k in claim_doc_lower for k in ["datasheet", "ds-", "oem", "supplier", "component", "spec"])
        claim_scope = (claim.entity_scope or "System").lower()

        # Scope Check: If claim is from a component datasheet (e.g. ASIC) but requirement is system/pack level (e.g. BCU/Pack)
        # and not specifically about the ASIC component, do not create a false conflict if system test passes or scope mismatch
        if is_datasheet and claim_scope != "system" and contract_scope != "system":
            if claim_scope != contract_scope and not (claim_scope in contract_title_lower or claim_scope in contract_raw_lower):
                continue

        if is_datasheet and "asic" in claim_doc_lower and ("bcu pack" in contract_title_lower or "pack operating" in contract_title_lower or "pack operational" in contract_title_lower):
            if "asic" not in contract_title_lower and not ("cell supervisory asic" in contract_raw_lower and "standoff" in contract_raw_lower):
                if has_system_empirical_support:
                    continue

        # 1. Numeric Range upper/lower limit restriction (e.g. Spec 1000V ASIC Standoff vs Datasheet 750V max, or Temp +85C vs +70C)
        if contract.requirement_type in ("numeric_range", "threshold"):
            if claim.claim_type in ("numeric_range", "threshold") and (claim.max_value is not None or claim.value is not None):
                claim_lim = claim.max_value if claim.max_value is not None else float(claim.value) # type: ignore
                if are_units_compatible(claim.unit, contract.unit):
                    c_claim_max = convert_value(claim_lim, claim.unit, contract.unit)
                    
                    is_conflict = False
                    reason_detail = ""
                    
                    if c_claim_max is not None and contract.max_value is not None and c_claim_max < contract.max_value - 0.5:
                        is_conflict = True
                        reason_detail = f"Specification mandates operation up to {contract.max_value:g} {contract.unit or ''}, but {claim.document_name} restricts maximum rated operation to {c_claim_max:g} {contract.unit or ''}."
                    elif c_claim_max is not None and contract.min_value is not None and contract.operator in (">=", ">", "between", None) and c_claim_max < contract.min_value - 0.5:
                        is_conflict = True
                        reason_detail = f"Specification mandates capability of at least {contract.min_value:g} {contract.unit or ''}, but {claim.document_name} restricts maximum rated operation to {c_claim_max:g} {contract.unit or ''}."

                    if is_conflict:
                        contract_terms = [w.lower() for w in re.findall(r"\w+", f"{contract.req_code} {contract.title}") if len(w) > 3 and w.lower() not in ["operating", "temperature", "voltage", "ambient", "system", "continuous", "window"]]
                        claim_param = (claim.parameter or "").lower()
                        if not contract_terms or any(t in claim.quote.lower() for t in contract_terms) or any(t in claim_param for t in contract_terms) or any(t in claim_doc_lower for t in contract_terms):
                            if is_datasheet:
                                highlight = f"{claim_lim:g} {claim.unit or ''}".strip()
                                desc = (
                                    f"Direct parameter discrepancy identified between {contract.title} and {claim.document_name}. "
                                    f"{reason_detail}"
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
