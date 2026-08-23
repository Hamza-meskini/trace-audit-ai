"""Coverage classification service.

Combines structured requirement contracts, factual evidence claims,
modular deterministic validators, and contradiction detection into a
conservative, auditable requirement coverage decision engine.

All entry points (sync, async, batched) share the same deterministic
pre-check chain and finalization logic. The async and batched variants
additionally escalate inconclusive (UNKNOWN) cases to the LLM
verification reasoner.
"""

from typing import Optional, Any
from dataclasses import dataclass, field

from app.schemas.contract import RequirementContract, parse_requirement_contract
from app.schemas.claim import extract_all_evidence_claims
from app.services.contradiction import detect_cross_document_contradiction, ContradictionFinding
from app.services.validators.numeric_range import validate_numeric_range
from app.services.validators.threshold import validate_threshold
from app.services.validators.duration import validate_duration
from app.services.validators.boolean_flag import validate_boolean_flag
from app.services.validators.test_verdict import validate_test_verdict
from app.services.validators.semantic import validate_semantic
from app.services.validators import ValidationOutcome
from app.services.verification_reasoner import SPEC_DOC_KEYWORDS


@dataclass
class EvidenceLinkAssessment:
    chunk_id: str
    document_name: str
    page_number: Optional[int]
    quote: str
    status: str  # "Supports requirement" | "Potential conflict" | "Supporting evidence"
    label: str
    highlight: Optional[str] = None


@dataclass
class RequirementAssessment:
    coverage_status: str  # "Supported" | "Partial" | "Missing" | "Conflict"
    confidence: float
    review_state: str     # "Reviewed" | "Needs review" | "Open"
    ai_analysis: str
    ai_recommendation: str
    evidence_links: list[EvidenceLinkAssessment] = field(default_factory=list)
    contract: Optional[RequirementContract] = None


RECOMMENDATIONS = {
    "Supported": "No immediate action required. Retain the current evidence set for the technical compliance file.",
    "Partial": "Extend qualification testing to cover remaining parameter bounds or attach completed test records.",
    "Conflict": "Review contradictory technical documentation with engineering stakeholders.",
    "Missing": "Upload the relevant test plan, test report, or compliance record covering this requirement.",
}


def _format_evidence_items(candidate_chunks: list[dict]) -> list[dict]:
    """Normalize candidate chunks into the evidence-item dict used by all checks."""
    return [
        {
            "chunk_id": c.get("id") or c.get("chunk_id", ""),
            "document_name": c.get("document_name", "Document"),
            "doc_type": c.get("doc_type", "Document"),
            "page_number": c.get("page_number"),
            "quote": c.get("content") or c.get("quote", ""),
        }
        for c in candidate_chunks
    ]


def _filter_non_spec(
    evidence_items: list[dict],
    spec_doc_names: Optional[set[str]] = None,
) -> list[dict]:
    """Drop self-referential specification chunks (SRS / product requirements docs / design constraints)."""
    if spec_doc_names:
        return [
            e for e in evidence_items
            if e.get("document_name") not in spec_doc_names
            and not any(k in e.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS)
        ]
    return [
        e for e in evidence_items
        if not any(k in e.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS)
    ]


def _missing_assessment(contract: RequirementContract, empty_index: bool = False) -> RequirementAssessment:
    if empty_index:
        analysis = "No evidence segment in the indexed document set addresses this requirement."
    else:
        analysis = "No independent test report, component datasheet, or compliance matrix was found verifying this requirement."
    return RequirementAssessment(
        coverage_status="Missing",
        confidence=95.0,
        review_state="Open",
        ai_analysis=analysis,
        ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
        evidence_links=[],
        contract=contract,
    )


def _conflict_assessment(
    contract: RequirementContract,
    evidence_items: list[dict],
    contradiction: ContradictionFinding,
) -> RequirementAssessment:
    links = []
    for e in evidence_items:
        is_conflict_source = contradiction.highlight and contradiction.highlight.lower() in e["quote"].lower()
        status = "Potential conflict" if is_conflict_source else "Supports requirement"
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"].replace(".pdf", "").replace(".docx", "")
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status=status,
            label=label,
            highlight=contradiction.highlight if is_conflict_source else None,
        ))

    return RequirementAssessment(
        coverage_status="Conflict",
        confidence=92.0,
        review_state="Needs review",
        ai_analysis=contradiction.description,
        ai_recommendation="Review the conflicting documentation with engineering and confirm the verified operating bounds before sign-off.",
        evidence_links=links,
        contract=contract,
    )


def _verdict_assessment(contract: RequirementContract, verdict_outcome: ValidationOutcome) -> RequirementAssessment:
    status_map = {
        "MISSING": "Missing",
        "PARTIAL": "Partial",
        "CONFLICT": "Conflict",
    }
    cov_status = status_map.get(verdict_outcome.status, "Partial")
    rev_state = "Open" if cov_status == "Missing" else "Needs review"
    return RequirementAssessment(
        coverage_status=cov_status,
        confidence=verdict_outcome.confidence,
        review_state=rev_state,
        ai_analysis=verdict_outcome.reason,
        ai_recommendation="Schedule testing or review in-progress validation records." if cov_status != "Conflict" else "Investigate test failure root cause.",
        evidence_links=[],
        contract=contract,
    )


def _run_deterministic_validators(contract: RequirementContract, claims: list) -> Optional[ValidationOutcome]:
    """Run type-specific deterministic validators, falling back to semantic validation."""
    outcome: Optional[ValidationOutcome] = None

    if contract.requirement_type == "numeric_range":
        outcome = validate_numeric_range(contract, claims)
    elif contract.requirement_type == "duration":
        outcome = validate_duration(contract, claims)
    elif contract.requirement_type == "threshold":
        outcome = validate_threshold(contract, claims)
    elif contract.requirement_type in ("boolean", "enumeration"):
        outcome = validate_boolean_flag(contract, claims)

    if outcome is None:
        outcome = validate_semantic(contract, claims)
    return outcome


def _deterministic_prechecks(
    contract: RequirementContract,
    candidate_chunks: list[dict],
    spec_doc_names: Optional[set[str]] = None,
) -> tuple[Optional[tuple[list[dict], list[dict], list]], Optional[RequirementAssessment]]:
    """Shared deterministic pre-check chain: empty evidence, spec self-reference,
    cross-document contradictions, and compliance-matrix verdicts.

    Returns (None, assessment) when a conclusive decision is reached, otherwise
    ((evidence_items, non_spec_items, claims), None) for downstream validators.
    """
    if not candidate_chunks:
        return None, _missing_assessment(contract, empty_index=True)

    evidence_items = _format_evidence_items(candidate_chunks)
    non_spec_items = _filter_non_spec(evidence_items, spec_doc_names=spec_doc_names)

    # If only specification self-chunks were retrieved, no independent test record exists
    if not non_spec_items:
        return None, _missing_assessment(contract)

    claims = extract_all_evidence_claims(evidence_items, contract)

    # Cross-document / contract contradictions -> CONFLICT
    contradiction: Optional[ContradictionFinding] = detect_cross_document_contradiction(evidence_items, contract)
    if contradiction and contradiction.has_conflict:
        return None, _conflict_assessment(contract, evidence_items, contradiction)

    # Formal compliance matrix test verdicts (e.g. NOT STARTED, IN PROGRESS, PASS, FAIL)
    verdict_outcome = validate_test_verdict(contract, claims)
    if verdict_outcome and verdict_outcome.status in ("MISSING", "PARTIAL", "CONFLICT"):
        return None, _verdict_assessment(contract, verdict_outcome)

    return (evidence_items, non_spec_items, claims), None


def _finalize_assessment(
    contract: RequirementContract,
    non_spec_items: list[dict],
    outcome: Optional[ValidationOutcome],
) -> RequirementAssessment:
    """Map a validation outcome into a RequirementAssessment with evidence links."""
    status_mapping = {
        "SUPPORTED": ("Supported", "Reviewed"),
        "PARTIAL": ("Partial", "Needs review"),
        "CONFLICT": ("Conflict", "Needs review"),
        "MISSING": ("Missing", "Open"),
        "UNKNOWN": ("Partial", "Needs review"),  # Conservative mapping for UI
    }

    if outcome is None:
        outcome = ValidationOutcome(
            status="UNKNOWN",
            confidence=70.0,
            reason="Evidence could not be deterministically verified.",
        )

    cov_status, rev_state = status_mapping.get(outcome.status, ("Partial", "Needs review"))

    highlight = outcome.highlight
    links = []
    for e in non_spec_items:
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
        is_highlight_src = highlight and highlight.lower() in e["quote"].lower()
        link_status = "Potential conflict" if is_highlight_src else ("Supports requirement" if cov_status == "Supported" else "Supporting evidence")
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status=link_status,
            label=label,
            highlight=highlight if is_highlight_src else None,
        ))

    return RequirementAssessment(
        coverage_status=cov_status,
        confidence=outcome.confidence,
        review_state=rev_state,
        ai_analysis=outcome.reason,
        ai_recommendation=RECOMMENDATIONS.get(cov_status, "Perform engineering review."),
        evidence_links=links,
        contract=contract,
    )


def assess_requirement_coverage(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
    spec_doc_names: Optional[set[str]] = None,
) -> RequirementAssessment:
    """Assess a requirement using the deterministic validation engine only (no LLM calls)."""
    contract = parse_requirement_contract(
        req_code=req_code,
        title=title,
        description=description,
        category=category,
    )

    context, decided = _deterministic_prechecks(contract, candidate_chunks, spec_doc_names=spec_doc_names)
    if decided:
        return decided

    evidence_items, non_spec_items, claims = context

    validation_outcome = _run_deterministic_validators(contract, claims)

    # If outcome is UNKNOWN, run the deterministic multi-condition reasoner
    if validation_outcome and validation_outcome.status == "UNKNOWN":
        from app.services.verification_reasoner import rule_based_multi_condition_verification
        reasoner_result = rule_based_multi_condition_verification(contract, candidate_chunks, spec_doc_names=spec_doc_names)
        if reasoner_result.status in ("SUPPORTED", "PARTIAL", "CONFLICT", "MISSING"):
            validation_outcome = ValidationOutcome(
                status=reasoner_result.status,
                confidence=float(reasoner_result.confidence),
                reason=reasoner_result.reason,
                highlight=reasoner_result.highlight,
            )

    return _finalize_assessment(contract, non_spec_items, validation_outcome)


async def assess_requirement_coverage_async(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    spec_doc_names: Optional[set[str]] = None,
) -> RequirementAssessment:
    """Async assessment that escalates inconclusive cases to the LLM verification reasoner."""
    contract = parse_requirement_contract(
        req_code=req_code,
        title=title,
        description=description,
        category=category,
    )

    context, decided = _deterministic_prechecks(contract, candidate_chunks, spec_doc_names=spec_doc_names)
    if decided:
        return decided

    evidence_items, non_spec_items, claims = context

    validation_outcome = _run_deterministic_validators(contract, claims)

    # If deterministic validation did not produce an authoritative answer, escalate to the LLM reasoner
    if validation_outcome is None or validation_outcome.status == "UNKNOWN":
        from app.services.verification_reasoner import evaluate_requirement_verification
        reasoner_result = await evaluate_requirement_verification(
            contract=contract,
            evidence_chunks=candidate_chunks,
            model=model,
            thinking_level=thinking_level,
            spec_doc_names=spec_doc_names,
        )
        validation_outcome = ValidationOutcome(
            status=reasoner_result.status,
            confidence=float(reasoner_result.confidence),
            reason=reasoner_result.reason,
            highlight=reasoner_result.highlight,
        )

    return _finalize_assessment(contract, non_spec_items, validation_outcome)


async def batch_assess_requirements(
    req_items: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    batch_size: int = 10,
    spec_doc_names: Optional[set[str]] = None,
) -> dict[str, RequirementAssessment]:
    """Assess a batch of requirements: deterministic checks first, batched LLM reasoning for the rest.

    Batches inconclusive requirements in groups of `batch_size` to limit rate-limit
    pressure while maintaining full multi-condition semantic reasoning.
    """
    from app.services.verification_reasoner import evaluate_batch_verification

    assessments: dict[str, RequirementAssessment] = {}
    pre_processed = []

    # Step 1: deterministic pre-checks and type-specific validators per requirement
    for item in req_items:
        req_code = item["req_code"]
        contract = parse_requirement_contract(
            req_code=req_code,
            title=item.get("title", ""),
            description=item.get("description", ""),
            category=item.get("category", "General"),
        )
        candidate_chunks = item.get("candidate_chunks", [])

        context, decided = _deterministic_prechecks(contract, candidate_chunks, spec_doc_names=spec_doc_names)
        if decided:
            assessments[req_code] = decided
            continue

        evidence_items, non_spec_items, claims = context

        val_outcome = _run_deterministic_validators(contract, claims)
        if val_outcome and val_outcome.status != "UNKNOWN":
            # Deterministic validators reached an authoritative conclusion
            assessments[req_code] = _finalize_assessment(contract, non_spec_items, val_outcome)
            continue

        # Not resolved deterministically -> queue for batched LLM reasoning
        pre_processed.append({
            "req_code": req_code,
            "contract": contract,
            "candidate_chunks": candidate_chunks,
            "non_spec_items": non_spec_items,
        })

    # Step 2: process queued requirements in batches
    for i in range(0, len(pre_processed), batch_size):
        batch = pre_processed[i : i + batch_size]
        batch_results = await evaluate_batch_verification(
            batch_items=batch,
            model=model,
            thinking_level=thinking_level,
        )

        for item in batch:
            req_code = item["req_code"]
            res = batch_results.get(req_code)
            if res:
                outcome = ValidationOutcome(
                    status=res.status,
                    confidence=float(res.confidence),
                    reason=res.reason,
                    highlight=res.highlight,
                )
            else:
                outcome = ValidationOutcome(
                    status="UNKNOWN",
                    confidence=75.0,
                    reason="Evaluated through compliance assessment engine.",
                )
            assessments[req_code] = _finalize_assessment(item["contract"], item["non_spec_items"], outcome)

    return assessments
