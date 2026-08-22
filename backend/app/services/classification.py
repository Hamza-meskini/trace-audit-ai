"""Coverage classification service.

Combines structured requirement contracts, factual evidence claims,
modular deterministic validators, and contradiction detection into a
conservative, auditable requirement coverage decision engine.
"""

import re
from typing import Optional, Any
from dataclasses import dataclass, field

from app.schemas.contract import RequirementContract, parse_requirement_contract
from app.schemas.claim import EvidenceClaim, extract_all_evidence_claims
from app.services.contradiction import detect_cross_document_contradiction, ContradictionFinding
from app.services.validators.numeric_range import validate_numeric_range
from app.services.validators.threshold import validate_threshold
from app.services.validators.duration import validate_duration
from app.services.validators.boolean_flag import validate_boolean_flag
from app.services.validators.test_verdict import validate_test_verdict
from app.services.validators.semantic import validate_semantic
from app.services.validators import ValidationOutcome


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


def assess_requirement_coverage(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
) -> RequirementAssessment:
    """Assess a requirement against candidate evidence chunks using the deterministic validation engine."""
    # 1. Parse structured RequirementContract
    contract = parse_requirement_contract(
        req_code=req_code,
        title=title,
        description=description,
        category=category,
    )

    # 2. Handle empty candidates
    if not candidate_chunks:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=95.0,
            review_state="Open",
            ai_analysis="No evidence segment in the indexed document set addresses this requirement.",
            ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
            evidence_links=[],
            contract=contract,
        )

    # Format evidence items for checks
    evidence_items = []
    for c in candidate_chunks:
        evidence_items.append({
            "chunk_id": c.get("id") or c.get("chunk_id", ""),
            "document_name": c.get("document_name", "Document"),
            "doc_type": c.get("doc_type", "Document"),
            "page_number": c.get("page_number"),
            "quote": c.get("content") or c.get("quote", ""),
        })

    # Separate non-specification evidence items
    non_spec_items = [
        e for e in evidence_items
        if not any(k in e["document_name"].lower() for k in ["srs", "product_requirements", "requirements_specification"])
    ]

    # If only specification self-chunks were retrieved, no independent test record exists
    if not non_spec_items:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=95.0,
            review_state="Open",
            ai_analysis="No independent test report, component datasheet, or compliance matrix was found verifying this requirement.",
            ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
            evidence_links=[],
            contract=contract,
        )

    # 3. Extract factual EvidenceClaims from all candidate chunks
    claims = extract_all_evidence_claims(evidence_items, contract)

    # 4. Check for cross-document / contract contradictions -> CONFLICT
    contradiction: Optional[ContradictionFinding] = detect_cross_document_contradiction(evidence_items, contract)
    if contradiction and contradiction.has_conflict:
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

    # 5. Check formal compliance matrix test verdicts (e.g. NOT STARTED, IN PROGRESS, PASS, FAIL)
    verdict_outcome = validate_test_verdict(contract, claims)
    if verdict_outcome and verdict_outcome.status in ("MISSING", "PARTIAL", "CONFLICT"):
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

    # 6. Execute type-specific deterministic validators
    validation_outcome: Optional[ValidationOutcome] = None

    if contract.requirement_type == "numeric_range":
        validation_outcome = validate_numeric_range(contract, claims)
    elif contract.requirement_type == "duration":
        validation_outcome = validate_duration(contract, claims)
    elif contract.requirement_type == "threshold":
        validation_outcome = validate_threshold(contract, claims)
    elif contract.requirement_type in ("boolean", "enumeration"):
        validation_outcome = validate_boolean_flag(contract, claims)

    # Fallback to semantic validation if no numeric validator fired or if type is semantic
    if validation_outcome is None:
        validation_outcome = validate_semantic(contract, claims)

    # If outcome is UNKNOWN, run multi-condition evidence reasoner
    if validation_outcome and validation_outcome.status == "UNKNOWN":
        from app.services.verification_reasoner import rule_based_multi_condition_verification
        reasoner_result = rule_based_multi_condition_verification(contract, candidate_chunks)
        if reasoner_result.status in ("SUPPORTED", "PARTIAL", "CONFLICT", "MISSING"):
            validation_outcome = ValidationOutcome(
                status=reasoner_result.status,
                confidence=float(reasoner_result.confidence),
                reason=reasoner_result.reason,
                highlight=reasoner_result.highlight,
            )

    # 7. Map validation outcome into RequirementAssessment
    status_mapping = {
        "SUPPORTED": ("Supported", "Reviewed"),
        "PARTIAL": ("Partial", "Needs review"),
        "CONFLICT": ("Conflict", "Needs review"),
        "MISSING": ("Missing", "Open"),
        "UNKNOWN": ("Partial", "Needs review"),  # Conservative mapping for UI
    }

    cov_status, rev_state = status_mapping.get(validation_outcome.status, ("Partial", "Needs review"))

    # Build evidence links
    links = []
    for e in non_spec_items:
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
        is_highlight_src = validation_outcome.highlight and validation_outcome.highlight.lower() in e["quote"].lower()
        link_status = "Potential conflict" if is_highlight_src else ("Supports requirement" if cov_status == "Supported" else "Supporting evidence")
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status=link_status,
            label=label,
            highlight=validation_outcome.highlight if is_highlight_src else None,
        ))

    recommendation = {
        "Supported": "No immediate action required. Retain the current evidence set for the technical compliance file.",
        "Partial": "Extend qualification testing to cover remaining parameter bounds or attach completed test records.",
        "Conflict": "Review contradictory technical documentation with engineering stakeholders.",
        "Missing": "Upload the relevant test plan, test report, or compliance record covering this requirement.",
    }.get(cov_status, "Perform engineering review.")

    return RequirementAssessment(
        coverage_status=cov_status,
        confidence=validation_outcome.confidence,
        review_state=rev_state,
        ai_analysis=validation_outcome.reason,
        ai_recommendation=recommendation,
        evidence_links=links,
        contract=contract,
    )


async def assess_requirement_coverage_async(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
    model: Optional[str] = "gemini-3.7-flash",
    thinking_level: Optional[str] = "HIGH",
) -> RequirementAssessment:
    """Async assessment that uses Gemini 3.7 Flash for multi-condition semantic reasoning."""
    # 1. Parse structured RequirementContract
    contract = parse_requirement_contract(
        req_code=req_code,
        title=title,
        description=description,
        category=category,
    )

    # 2. Handle empty candidates
    if not candidate_chunks:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=95.0,
            review_state="Open",
            ai_analysis="No evidence segment in the indexed document set addresses this requirement.",
            ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
            evidence_links=[],
            contract=contract,
        )

    # Format evidence items
    evidence_items = []
    for c in candidate_chunks:
        evidence_items.append({
            "chunk_id": c.get("id") or c.get("chunk_id", ""),
            "document_name": c.get("document_name", "Document"),
            "doc_type": c.get("doc_type", "Document"),
            "page_number": c.get("page_number"),
            "quote": c.get("content") or c.get("quote", ""),
        })

    non_spec_items = [
        e for e in evidence_items
        if not any(k in e["document_name"].lower() for k in ["srs", "product_requirements", "requirements_specification"])
    ]

    if not non_spec_items:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=95.0,
            review_state="Open",
            ai_analysis="No independent test report, component datasheet, or compliance matrix was found verifying this requirement.",
            ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
            evidence_links=[],
            contract=contract,
        )

    # 3. Extract factual EvidenceClaims
    claims = extract_all_evidence_claims(evidence_items, contract)

    # 4. Check for cross-document / contract contradictions -> CONFLICT
    contradiction: Optional[ContradictionFinding] = detect_cross_document_contradiction(evidence_items, contract)
    if contradiction and contradiction.has_conflict:
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

    # 5. Check formal compliance matrix test verdicts
    verdict_outcome = validate_test_verdict(contract, claims)
    if verdict_outcome and verdict_outcome.status in ("MISSING", "PARTIAL", "CONFLICT"):
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

    # 6. Execute type-specific deterministic validators
    validation_outcome: Optional[ValidationOutcome] = None

    if contract.requirement_type == "numeric_range":
        validation_outcome = validate_numeric_range(contract, claims)
    elif contract.requirement_type == "duration":
        validation_outcome = validate_duration(contract, claims)
    elif contract.requirement_type == "threshold":
        validation_outcome = validate_threshold(contract, claims)
    elif contract.requirement_type in ("boolean", "enumeration"):
        validation_outcome = validate_boolean_flag(contract, claims)

    # If deterministic validator did not produce an authoritative answer or outcome is UNKNOWN:
    if validation_outcome is None or validation_outcome.status == "UNKNOWN":
        from app.services.verification_reasoner import evaluate_requirement_verification
        reasoner_result = await evaluate_requirement_verification(
            contract=contract,
            evidence_chunks=candidate_chunks,
            model=model,
            thinking_level=thinking_level,
        )
        validation_outcome = ValidationOutcome(
            status=reasoner_result.status,
            confidence=float(reasoner_result.confidence),
            reason=reasoner_result.reason,
            highlight=reasoner_result.highlight,
        )

    # 7. Map validation outcome into RequirementAssessment
    status_mapping = {
        "SUPPORTED": ("Supported", "Reviewed"),
        "PARTIAL": ("Partial", "Needs review"),
        "CONFLICT": ("Conflict", "Needs review"),
        "MISSING": ("Missing", "Open"),
        "UNKNOWN": ("Partial", "Needs review"),
    }

    cov_status, rev_state = status_mapping.get(validation_outcome.status, ("Partial", "Needs review"))

    links = []
    for e in non_spec_items:
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
        is_highlight_src = validation_outcome.highlight and validation_outcome.highlight.lower() in e["quote"].lower()
        link_status = "Potential conflict" if is_highlight_src else ("Supports requirement" if cov_status == "Supported" else "Supporting evidence")
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status=link_status,
            label=label,
            highlight=validation_outcome.highlight if is_highlight_src else None,
        ))

    recommendation = {
        "Supported": "No immediate action required. Retain the current evidence set for the technical compliance file.",
        "Partial": "Extend qualification testing to cover remaining parameter bounds or attach completed test records.",
        "Conflict": "Review contradictory technical documentation with engineering stakeholders.",
        "Missing": "Upload the relevant test plan, test report, or compliance record covering this requirement.",
    }.get(cov_status, "Perform engineering review.")

    return RequirementAssessment(
        coverage_status=cov_status,
        confidence=validation_outcome.confidence,
        review_state=rev_state,
        ai_analysis=validation_outcome.reason,
        ai_recommendation=recommendation,
        evidence_links=links,
        contract=contract,
    )


async def batch_assess_requirements(
    req_items: list[dict[str, Any]],
    model: Optional[str] = "gemini-3.7-flash",
    thinking_level: Optional[str] = "HIGH",
    batch_size: int = 10,
) -> dict[str, RequirementAssessment]:
    """Assess a batch of requirements concurrently with Gemini 3.7 Flash batch prompting.
    
    Batches requirements in groups of 10 to eliminate 429 rate limit issues while maintaining
    full multi-condition semantic reasoning.
    """
    from app.services.verification_reasoner import evaluate_batch_verification

    assessments: dict[str, RequirementAssessment] = {}
    pre_processed = []

    # Step 1: Pre-process each requirement with deterministic checks
    for item in req_items:
        req_code = item["req_code"]
        title = item.get("title", "")
        description = item.get("description", "")
        category = item.get("category", "General")
        candidate_chunks = item.get("candidate_chunks", [])

        contract = parse_requirement_contract(
            req_code=req_code,
            title=title,
            description=description,
            category=category,
        )

        evidence_items = [
            {
                "chunk_id": c.get("id") or c.get("chunk_id", ""),
                "document_name": c.get("document_name", "Document"),
                "doc_type": c.get("doc_type", "Document"),
                "page_number": c.get("page_number"),
                "quote": c.get("content") or c.get("quote", ""),
            }
            for c in candidate_chunks
        ]

        non_spec_items = [
            e for e in evidence_items
            if not any(k in e["document_name"].lower() for k in ["srs", "product_requirements", "requirements_specification"])
        ]

        if not non_spec_items:
            assessments[req_code] = RequirementAssessment(
                coverage_status="Missing",
                confidence=95.0,
                review_state="Open",
                ai_analysis="No independent test report, component datasheet, or compliance matrix was found verifying this requirement.",
                ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
                evidence_links=[],
                contract=contract,
            )
            continue

        claims = extract_all_evidence_claims(evidence_items, contract)

        # Check for cross-document / contract contradictions
        contradiction = detect_cross_document_contradiction(evidence_items, contract)
        if contradiction and contradiction.has_conflict:
            links = []
            for e in evidence_items:
                is_conflict_src = contradiction.highlight and contradiction.highlight.lower() in e["quote"].lower()
                status = "Potential conflict" if is_conflict_src else "Supports requirement"
                label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"].replace(".pdf", "").replace(".docx", "")
                links.append(EvidenceLinkAssessment(
                    chunk_id=e["chunk_id"],
                    document_name=e["document_name"],
                    page_number=e["page_number"],
                    quote=e["quote"],
                    status=status,
                    label=label,
                    highlight=contradiction.highlight if is_conflict_src else None,
                ))
            assessments[req_code] = RequirementAssessment(
                coverage_status="Conflict",
                confidence=92.0,
                review_state="Needs review",
                ai_analysis=contradiction.description,
                ai_recommendation="Review contradictory documentation with engineering stakeholders.",
                evidence_links=links,
                contract=contract,
            )
            continue

        # Check compliance matrix test verdicts
        verdict_outcome = validate_test_verdict(contract, claims)
        if verdict_outcome and verdict_outcome.status in ("MISSING", "PARTIAL", "CONFLICT"):
            status_map = {"MISSING": "Missing", "PARTIAL": "Partial", "CONFLICT": "Conflict"}
            cov_status = status_map.get(verdict_outcome.status, "Partial")
            rev_state = "Open" if cov_status == "Missing" else "Needs review"
            assessments[req_code] = RequirementAssessment(
                coverage_status=cov_status,
                confidence=verdict_outcome.confidence,
                review_state=rev_state,
                ai_analysis=verdict_outcome.reason,
                ai_recommendation="Schedule testing or review validation records.",
                evidence_links=[],
                contract=contract,
            )
            continue

        # Deterministic validators
        val_outcome: Optional[ValidationOutcome] = None
        if contract.requirement_type == "numeric_range":
            val_outcome = validate_numeric_range(contract, claims)
        elif contract.requirement_type == "duration":
            val_outcome = validate_duration(contract, claims)
        elif contract.requirement_type == "threshold":
            val_outcome = validate_threshold(contract, claims)
        elif contract.requirement_type in ("boolean", "enumeration"):
            val_outcome = validate_boolean_flag(contract, claims)

        if val_outcome and val_outcome.status in ("SUPPORTED", "CONFLICT"):
            status_mapping = {"SUPPORTED": ("Supported", "Reviewed"), "CONFLICT": ("Conflict", "Needs review")}
            cov_status, rev_state = status_mapping.get(val_outcome.status, ("Partial", "Needs review"))
            assessments[req_code] = RequirementAssessment(
                coverage_status=cov_status,
                confidence=val_outcome.confidence,
                review_state=rev_state,
                ai_analysis=val_outcome.reason,
                ai_recommendation="No action needed." if cov_status == "Supported" else "Review conflict.",
                evidence_links=[],
                contract=contract,
            )
            continue

        # If not resolved deterministically, queue for batch LLM reasoning
        pre_processed.append({
            "req_code": req_code,
            "contract": contract,
            "candidate_chunks": candidate_chunks,
            "non_spec_items": non_spec_items,
        })

    # Step 2: Process queued requirements in batches of 10
    for i in range(0, len(pre_processed), batch_size):
        batch = pre_processed[i : i + batch_size]
        batch_results = await evaluate_batch_verification(
            batch_items=batch,
            model=model,
            thinking_level=thinking_level,
        )

        for item in batch:
            req_code = item["req_code"]
            contract = item["contract"]
            non_spec_items = item["non_spec_items"]
            res = batch_results.get(req_code)

            cov_status = res.status.capitalize() if res and res.status in ("SUPPORTED", "PARTIAL", "MISSING", "CONFLICT") else "Partial"
            rev_state = "Reviewed" if cov_status == "Supported" else ("Open" if cov_status == "Missing" else "Needs review")
            confidence = float(res.confidence) if res else 75.0
            reason = res.reason if res else "Evaluated through compliance assessment engine."
            highlight = res.highlight if res else None

            links = []
            for e in non_spec_items:
                label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
                is_hl = highlight and highlight.lower() in e["quote"].lower()
                link_status = "Potential conflict" if is_hl else ("Supports requirement" if cov_status == "Supported" else "Supporting evidence")
                links.append(EvidenceLinkAssessment(
                    chunk_id=e["chunk_id"],
                    document_name=e["document_name"],
                    page_number=e["page_number"],
                    quote=e["quote"],
                    status=link_status,
                    label=label,
                    highlight=highlight if is_hl else None,
                ))

            assessments[req_code] = RequirementAssessment(
                coverage_status=cov_status,
                confidence=confidence,
                review_state=rev_state,
                ai_analysis=reason,
                ai_recommendation="Retain evidence." if cov_status == "Supported" else "Review validation bounds.",
                evidence_links=links,
                contract=contract,
            )

    return assessments

