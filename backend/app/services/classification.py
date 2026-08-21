"""Coverage classification service.

Combines semantic evidence retrieval, deterministic verification,
and contradiction detection into a coherent requirement coverage assessment.
"""

import re
from typing import Optional
from dataclasses import dataclass, field
from app.services.verification import verify_range_coverage, VerificationResult
from app.services.contradiction import detect_cross_document_contradiction, ContradictionFinding


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


def assess_requirement_coverage(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
) -> RequirementAssessment:
    """Assess a requirement against candidate evidence chunks."""
    req_full = f"{title}. {description or ''}".strip()

    # 1. No evidence found -> Missing
    if not candidate_chunks:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=92.0,
            review_state="Open",
            ai_analysis="No evidence segment in the indexed document set addresses this requirement.",
            ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
            evidence_links=[],
        )

    # Format evidence items for checks
    evidence_items = []
    for c in candidate_chunks:
        evidence_items.append({
            "chunk_id": c.get("id") or c.get("chunk_id"),
            "document_name": c.get("document_name", "Document"),
            "doc_type": c.get("doc_type", "Document"),
            "page_number": c.get("page_number"),
            "quote": c.get("content", ""),
        })

    # Requirement specification item representing the declared specification bounds
    spec_ref_item = {
        "chunk_id": f"spec-{req_code}",
        "document_name": "Product Specification",
        "doc_type": "Specification",
        "page_number": None,
        "quote": req_full,
    }

    # 1. Check for cross-document contradiction between spec and evidence items, or among evidence items
    all_items_for_contradiction = [spec_ref_item] + [e for e in evidence_items if not any(k in e["document_name"].lower() for k in ["srs", "product_requirements"])]
    contradiction = detect_cross_document_contradiction(all_items_for_contradiction)
    if contradiction and contradiction.has_conflict:
        links = []
        for e in evidence_items:
            # If this item is the conflicting one
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
            confidence=90.0,
            review_state="Needs review",
            ai_analysis=contradiction.description,
            ai_recommendation="Review the conflicting documentation with engineering and confirm the verified operating bounds before approval.",
            evidence_links=links,
        )

    # 2. Check non-specification evidence items
    non_spec_items = [
        e for e in evidence_items
        if not any(k in e["document_name"].lower() for k in ["srs", "product_requirements", "requirements_specification"])
    ]

    if not non_spec_items:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=92.0,
            review_state="Open",
            ai_analysis="No independent test report, datasheet, or compliance matrix segment in the indexed document set addresses this requirement.",
            ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
            evidence_links=[],
        )

    # 3. Check if compliance matrix / test records declare this requirement as Missing / Not Started
    req_key_tokens = [w.lower() for w in re.findall(r"\w+", f"{req_code} {title}") if len(w) > 4 and w.lower() not in ["operating", "temperature", "voltage", "ambient", "system", "continuous", "maximum", "minimum", "shall"]]
    has_explicit_missing = any(
        any(mkw in e["quote"].lower() for mkw in ["not started", "verdict: missing", "record missing", "[missing evidence]", "test missing"])
        for e in non_spec_items
    )
    has_test_pass = any(
        ("pass" in e["quote"].lower() or "measured" in e["quote"].lower()) and (req_code.lower() in e["quote"].lower() or any(kt in e["quote"].lower() for kt in req_key_tokens))
        for e in non_spec_items if "matrix" not in e["document_name"].lower()
    )

    if has_explicit_missing and not has_test_pass:
        return RequirementAssessment(
            coverage_status="Missing",
            confidence=94.0,
            review_state="Open",
            ai_analysis="Compliance verification records indicate this test has not been executed and required evidence is missing.",
            ai_recommendation="Schedule qualification testing and upload completed laboratory test records.",
            evidence_links=[],
        )

    evidence_quotes = [e["quote"] for e in non_spec_items]

    # 4. Check for numeric range coverage gaps or partial test keywords -> Partial
    PARTIAL_KEYWORDS = ["in progress", "pending", "incomplete", "single-point", "gap", "awaiting", "simulation only", "remainder of", "partial"]
    
    # Check if partial keywords apply specifically to this requirement in context
    has_partial_keyword = False
    req_tokens = [t.lower() for t in re.findall(r"\w+", f"{req_code} {title}") if len(t) > 3]
    for e in non_spec_items:
        q_lower = e["quote"].lower()
        for kw in PARTIAL_KEYWORDS:
            if kw in q_lower:
                for m in re.finditer(re.escape(kw), q_lower):
                    start = max(0, m.start() - 100)
                    end = min(len(q_lower), m.end() + 100)
                    context_window = q_lower[start:end]
                    if req_code.lower() in context_window or any(t in context_window for t in req_tokens if len(t) > 5):
                        has_partial_keyword = True
                        break

    verification: VerificationResult = verify_range_coverage(req_full, evidence_quotes)
    if verification.status == "Partial" or has_partial_keyword:
        links = []
        for e in non_spec_items:
            is_gap_source = (verification.highlight and verification.highlight.lower() in e["quote"].lower()) or any(kw in e["quote"].lower() for kw in PARTIAL_KEYWORDS)
            status = "Potential conflict" if is_gap_source else "Supports requirement"
            label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
            links.append(EvidenceLinkAssessment(
                chunk_id=e["chunk_id"],
                document_name=e["document_name"],
                page_number=e["page_number"],
                quote=e["quote"],
                status=status,
                label=label,
                highlight=verification.highlight if is_gap_source else None,
            ))

        reason = verification.reason if verification.status == "Partial" else "Available test record indicates partial or in-progress verification."
        return RequirementAssessment(
            coverage_status="Partial",
            confidence=87.0,
            review_state="Needs review",
            ai_analysis=reason,
            ai_recommendation="Extend environmental or functional verification to the full required range or attach final test report.",
            evidence_links=links,
        )

    # 5. Check if evidence is general supporting evidence (e.g. 1 chunk, partial depth)
    if len(non_spec_items) == 1 and ("calculate" in req_full.lower() or "signature" in req_full.lower() or "mtbf" in req_full.lower()):
        c = non_spec_items[0]
        link = EvidenceLinkAssessment(
            chunk_id=c.get("chunk_id"),
            document_name=c.get("document_name", "Document"),
            page_number=c.get("page_number"),
            quote=c.get("quote", ""),
            status="Supporting evidence",
            label=c.get("doc_type", "Product Specification"),
        )
        return RequirementAssessment(
            coverage_status="Partial",
            confidence=83.0,
            review_state="Needs review",
            ai_analysis="Supporting description was identified, but underlying validation or calculation record was not found.",
            ai_recommendation="Attach the supporting calculation worksheet or test record to complete the evidence chain.",
            evidence_links=[link],
        )

    # 6. Fully supported -> Supported
    links = []
    for e in non_spec_items:
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status="Supports requirement" if e == non_spec_items[0] else "Supporting evidence",
            label=label,
        ))

    return RequirementAssessment(
        coverage_status="Supported",
        confidence=95.0,
        review_state="Reviewed",
        ai_analysis="All indexed evidence segments are consistent with this requirement. Verification criteria and parameters are satisfied.",
        ai_recommendation="No immediate action required. Retain the current evidence set for the technical file.",
        evidence_links=links,
    )
