"""Coverage classification service.

Combines structured requirement contracts, factual evidence claims,
modular deterministic validators, and contradiction detection into a
conservative, auditable requirement coverage decision engine.
"""

import re
from typing import Optional
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
