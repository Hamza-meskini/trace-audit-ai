"""Coverage classification service.

Combines semantic evidence retrieval, deterministic verification,
and contradiction detection into a coherent requirement coverage assessment.
"""

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

    evidence_quotes = [e["quote"] for e in evidence_items]

    # 2. Check for cross-document contradiction -> Conflict
    contradiction = detect_cross_document_contradiction(evidence_items)
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

    # 3. Check for numeric range coverage gaps -> Partial
    verification: VerificationResult = verify_range_coverage(req_full, evidence_quotes)
    if verification.status == "Partial":
        links = []
        for e in evidence_items:
            is_gap_source = verification.highlight and verification.highlight.lower() in e["quote"].lower()
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

        return RequirementAssessment(
            coverage_status="Partial",
            confidence=87.0,
            review_state="Needs review",
            ai_analysis=verification.reason,
            ai_recommendation="Extend environmental or functional verification to the full required range or align declared limits.",
            evidence_links=links,
        )

    # 4. Check if evidence is general supporting evidence (e.g. 1 chunk, partial depth)
    if len(candidate_chunks) == 1 and ("calculate" in req_full.lower() or "signature" in req_full.lower() or "mtbf" in req_full.lower()):
        c = candidate_chunks[0]
        link = EvidenceLinkAssessment(
            chunk_id=c.get("id") or c.get("chunk_id"),
            document_name=c.get("document_name", "Document"),
            page_number=c.get("page_number"),
            quote=c.get("content", ""),
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

    # 5. Fully supported -> Supported
    links = []
    for e in evidence_items:
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status="Supports requirement" if e == evidence_items[0] else "Supporting evidence",
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
