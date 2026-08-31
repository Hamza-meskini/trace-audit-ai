"""Document Role Classification and Specification Discovery Service.

Combines content-based heuristic analysis (normative requirement markers vs. empirical test markers)
with fast structured LLM classification to accurately identify:
1. Primary Requirement Specifications (from which requirements are extracted)
2. Empirical Test Reports (pass/fail validation evidence)
3. Component Datasheets (hardware operating constraints)
4. Compliance Matrices (verification status tracking)
5. Safety / Risk Assessments (hazard analysis)

This replaces fragile filename-only substring matching and handles arbitrary filenames
such as 'TechnicalSpec_V2.pdf', 'Design_Constraints.docx', 'System_SRS.pdf', or 'Pack_V1_Final.docx'.
"""

import re
import logging
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field
from app.config import settings
from app.services.llm_client import generate_structured

logger = logging.getLogger("traceaudit.classifier")

DocumentRoleType = Literal[
    "SPECIFICATION",
    "TEST_REPORT",
    "DATASHEET",
    "COMPLIANCE_MATRIX",
    "RISK_ASSESSMENT",
    "ARCHITECTURE",
    "OTHER_EVIDENCE",
]

DocumentVerificationBasis = Literal[
    "physical_test",
    "simulation",
    "calculation",
    "inspection",
    "normative",
    "design",
    "mixed",
    "unknown",
]

DocumentStatusType = Literal["FINAL", "DRAFT", "SUPERSEDED", "UNCONTROLLED", "UNKNOWN"]


class DocumentProfileEvidence(BaseModel):
    page_number: Optional[int] = None
    quote: str = Field(default="", description="Short verbatim cue supporting the profile")


class ClassifiedDocument(BaseModel):
    document_name: str
    role: DocumentRoleType
    confidence: float = Field(default=80.0, description="Confidence score 0-100")
    reason: str = Field(default="", description="Brief explanation of document role")
    issuer: Optional[str] = None
    document_status: DocumentStatusType = "UNKNOWN"
    verification_basis: DocumentVerificationBasis = "unknown"
    standards: list[str] = Field(default_factory=list)
    subject: Optional[str] = None
    classification_evidence: list[DocumentProfileEvidence] = Field(default_factory=list)


class DocumentClassificationResult(BaseModel):
    classifications: list[ClassifiedDocument] = Field(default_factory=list)


class DocumentProfile(BaseModel):
    """Content-derived identity stored once and reused by every evidence chunk."""

    document_name: str
    primary_role: DocumentRoleType
    confidence: float = Field(default=0.0, ge=0.0, le=100.0)
    reason: str = ""
    issuer: Optional[str] = None
    document_status: DocumentStatusType = "UNKNOWN"
    verification_basis: DocumentVerificationBasis = "unknown"
    standards: list[str] = Field(default_factory=list)
    subject: Optional[str] = None
    classification_evidence: list[DocumentProfileEvidence] = Field(default_factory=list)
    requires_review: bool = False
    classification_source: str = "content_heuristic"


ROLE_DISPLAY_NAMES: dict[str, str] = {
    "SPECIFICATION": "Technical specification",
    "TEST_REPORT": "Test report",
    "DATASHEET": "Supplier documentation",
    "COMPLIANCE_MATRIX": "Compliance matrix",
    "RISK_ASSESSMENT": "Risk assessment",
    "ARCHITECTURE": "Architecture specification",
    "OTHER_EVIDENCE": "Technical documentation",
}


# ── Content-Based Scoring Markers ───────────────────────────────────────────

NORMATIVE_SPEC_MARKERS = [
    r"\bshall\b",
    r"\bmust\b",
    r"\brequired\s+to\b",
    r"\bshall\s+not\b",
    r"\brequirement[s]?\b",
    r"\bdesign\s+constraint[s]?\b",
    r"\bacceptance\s+criteria\b",
    r"\bfunctional\s+requirement[s]?\b",
    r"\bperformance\s+specification[s]?\b",
    r"\boperating\s+range\s+shall\b",
    r"\bREQ[-_]?[A-Za-z0-9_-]*\d+\b",
]

TEST_VERDICT_MARKERS = [
    r"\bfinal\s+test\s+report\b",
    r"\bsummary\s+of\s+test\s+results\b",
    r"\bverdict\s*:\s*(?:pass|fail)\b",
    r"\btest\s+result[s]?\s*:\s*(?:pass|fail|passed|failed)\b",
    r"\bmeasured\s+value[s]?\b",
    r"\btest\s+procedure\b",
    r"\blab\s+(?:report|testing|measurements)\b",
    r"\btest\s+setup\b",
    r"\bexecuted\s+on\b",
    r"\bserial\s+number\b",
    r"\btest\s+verdict\b",
    r"\bdata\s+sheet\s+no\.?",
    r"\btest\s+date\s*:",
    r"\btest\s+vehicle\s*:",
    r"\btest\s+program\s*:",
    r"\bpre[- ]test\b",
    r"\bpost[- ]test\b",
    r"\bappears\s+to\s+meet\b",
]

DATASHEET_MARKERS = [
    r"\babsolute\s+maximum\s+ratings\b",
    r"\belectrical\s+characteristics\b",
    r"\bpin\s+configuration\b",
    r"\bpackage\s+dimensions\b",
    r"\bordering\s+information\b",
    r"\btypical\s+application\s+circuit\b",
    r"\bcomponent\s+datasheet\b",
    r"\boem\s+supplier\b",
]

COMPLIANCE_MATRIX_MARKERS = [
    r"\bcompliance\s+matrix\b",
    r"\bverification\s+matrix\b",
    r"\btraceability\s+matrix\b",
    r"\bverification\s+method\b",
    r"\bstatus\s*:\s*(?:not\s+started|in\s+progress|complete)\b",
    r"\bevidence\s+reference\b",
]


def score_document_content(
    text: str,
    filename: str,
    include_filename_hints: bool = True,
) -> dict[str, float]:
    """Score document text across technical roles using regex density analysis."""
    text_lower = text.lower()
    fn_lower = filename.lower()

    # Spec score: boosted by normative keywords and requirement IDs
    spec_matches = sum(len(re.findall(pat, text_lower)) for pat in NORMATIVE_SPEC_MARKERS)
    spec_score = spec_matches * 1.5
    if include_filename_hints and any(k in fn_lower for k in ("spec", "requirement", "srs", "prd", "prs", "constraint", "system_definition")):
        spec_score += 10.0

    # Test report score: boosted by test verdicts and lab markers
    test_matches = sum(len(re.findall(pat, text_lower)) for pat in TEST_VERDICT_MARKERS)
    test_score = test_matches * 2.0
    # Cover-page and results-section declarations are stronger than incidental
    # normative "shall" quotations copied into a report.
    if re.search(r"\bfinal\s+test\s+report\b", text_lower):
        test_score += 30.0
    if re.search(r"\bsummary\s+of\s+test\s+results\b", text_lower):
        test_score += 20.0
    if include_filename_hints and any(k in fn_lower for k in ("test", "report", "lab", "validation", "verification_report", "tr-")):
        test_score += 10.0

    # Datasheet score: boosted by component ratings
    ds_matches = sum(len(re.findall(pat, text_lower)) for pat in DATASHEET_MARKERS)
    ds_score = ds_matches * 2.5
    if include_filename_hints and any(k in fn_lower for k in ("datasheet", "ds-", "supplier", "oem", "component", "ics-")):
        ds_score += 10.0

    # Matrix score
    matrix_matches = sum(len(re.findall(pat, text_lower)) for pat in COMPLIANCE_MATRIX_MARKERS)
    matrix_score = matrix_matches * 3.0
    if include_filename_hints and any(k in fn_lower for k in ("matrix", "compliance_verification", "vcvm", "rvtm")):
        matrix_score += 12.0

    return {
        "SPECIFICATION": spec_score,
        "TEST_REPORT": test_score,
        "DATASHEET": ds_score,
        "COMPLIANCE_MATRIX": matrix_score,
    }


def _profile_excerpt(chunks: list[dict[str, Any]], max_chars: int = 6500) -> str:
    """Sample cover, role-bearing, and final pages without relying on a filename."""
    if not chunks:
        return ""
    role_pattern = re.compile(
        r"final\s+test\s+report|test\s+results?|data\s+sheet|shall\b|"
        r"compliance\s+matrix|validation|laboratory|prepared\s+by|issued\s+by|"
        r"standard\s+no\.|fmvss|49\s+cfr|verdict|measured|calculated",
        re.IGNORECASE,
    )
    selected: list[dict[str, Any]] = []
    selected.extend(chunks[:2])
    selected.extend(chunk for chunk in chunks if role_pattern.search(chunk.get("content", "")))
    selected.extend(chunks[-1:])

    seen: set[Any] = set()
    blocks: list[str] = []
    size = 0
    for chunk in selected:
        key = chunk.get("id") or (chunk.get("page_number"), chunk.get("content", "")[:80])
        if key in seen:
            continue
        seen.add(key)
        page = chunk.get("page_number")
        block = f"[PAGE {page if page is not None else '?'}]\n{chunk.get('content', '').strip()}"
        remaining = max_chars - size
        if remaining <= 0:
            break
        blocks.append(block[:remaining])
        size += len(blocks[-1])
    return "\n\n".join(blocks)


def _heuristic_profile(document_name: str, chunks: list[dict[str, Any]]) -> DocumentProfile:
    full_text = "\n".join(chunk.get("content", "") for chunk in chunks)
    # Content decides first. Filename hints are used only if content has no
    # meaningful role signal at all.
    scores = score_document_content(full_text, document_name, include_filename_hints=False)
    if max(scores.values(), default=0.0) <= 0.0:
        scores = score_document_content(full_text, document_name, include_filename_hints=True)
    role, top_score = max(scores.items(), key=lambda item: item[1])
    ordered = sorted(scores.values(), reverse=True)
    runner_up = ordered[1] if len(ordered) > 1 else 0.0
    separation = (top_score - runner_up) / max(top_score, 1.0)
    confidence = min(95.0, 55.0 + 35.0 * max(0.0, separation)) if top_score else 35.0

    lower = full_text.lower()
    if role == "TEST_REPORT":
        basis: DocumentVerificationBasis = "physical_test"
    elif role == "SPECIFICATION":
        basis = "normative"
    elif role == "DATASHEET":
        basis = "design"
    elif role == "COMPLIANCE_MATRIX":
        basis = "mixed"
    else:
        basis = "unknown"

    status: DocumentStatusType = "UNKNOWN"
    if re.search(r"\bfinal(?:\s+test)?\s+report\b|\bdocument\s+status\s*:\s*final\b", lower):
        status = "FINAL"
    elif re.search(r"\bdraft\b|\bpreliminary\b", lower):
        status = "DRAFT"
    elif re.search(r"\bsuperseded\b|\bobsolete\b", lower):
        status = "SUPERSEDED"

    standards = sorted(set(
        match.group(0).strip()
        for match in re.finditer(
            r"(?:FMVSS(?:\s+No\.)?\s*\d+[a-z]?|49\s+CFR\s+§?\s*571\.\d+[a-z]?|ISO\s+\d+(?:-\d+)*)",
            full_text,
            re.IGNORECASE,
        )
    ))[:8]

    evidence: list[DocumentProfileEvidence] = []
    cue_pattern = re.compile(
        r"final\s+test\s+report|test\s+results?|laboratory|standard\s+no\.|"
        r"fmvss|compliance\s+matrix|absolute\s+maximum\s+ratings|\bshall\b",
        re.IGNORECASE,
    )
    for chunk in chunks:
        for line in chunk.get("content", "").splitlines():
            cleaned = " ".join(line.split())
            if cleaned and cue_pattern.search(cleaned):
                evidence.append(DocumentProfileEvidence(
                    page_number=chunk.get("page_number"),
                    quote=cleaned[:240],
                ))
                break
        if len(evidence) >= 4:
            break

    return DocumentProfile(
        document_name=document_name,
        primary_role=role,  # type: ignore[arg-type]
        confidence=round(confidence, 1),
        reason=f"Content role scores: {', '.join(f'{key}={value:.1f}' for key, value in scores.items())}.",
        document_status=status,
        verification_basis=basis,
        standards=standards,
        classification_evidence=evidence,
        requires_review=confidence < 65.0,
        classification_source="content_heuristic",
    )


def _profile_from_classification(
    classification: ClassifiedDocument,
    fallback: DocumentProfile,
) -> DocumentProfile:
    confidence = max(0.0, min(100.0, float(classification.confidence)))
    evidence = classification.classification_evidence or fallback.classification_evidence
    return DocumentProfile(
        document_name=classification.document_name,
        primary_role=classification.role,
        confidence=confidence,
        reason=classification.reason or fallback.reason,
        issuer=classification.issuer or fallback.issuer,
        document_status=(
            classification.document_status
            if classification.document_status != "UNKNOWN"
            else fallback.document_status
        ),
        verification_basis=(
            classification.verification_basis
            if classification.verification_basis != "unknown"
            else fallback.verification_basis
        ),
        standards=classification.standards or fallback.standards,
        subject=classification.subject or fallback.subject,
        classification_evidence=evidence,
        requires_review=confidence < 65.0 or not evidence,
        classification_source="llm_content_profile",
    )


# ── LLM-Assisted Classification ──────────────────────────────────────────────

async def classify_documents_with_llm(
    doc_excerpts: list[dict[str, str]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> dict[str, ClassifiedDocument]:
    """Classify multiple uploaded documents by role using Gemini/OpenAI."""
    active_model = model or settings.LLM_MODEL
    has_keys = bool(
        settings.effective_gemini_api_key
        or settings.effective_databricks_token
        or settings.effective_openai_api_key
    )

    if not has_keys or not doc_excerpts:
        return {}

    docs_block = []
    for i, d in enumerate(doc_excerpts, 1):
        name = d.get("filename", f"Doc_{i}")
        excerpt = (d.get("excerpt", "")[:6500]).strip()
        docs_block.append(f"--- Document #{i}: {name} ---\n{excerpt}\n")

    prompt = f"""You are an expert system auditor analyzing technical audit files.
Classify each of the following uploaded documents into exactly one of these roles:
- SPECIFICATION: Defines what the product SHALL do (product requirements, system SRS, PRD, design constraints, functional specs).
- TEST_REPORT: Empirical proof of testing (lab test logs, environmental chamber runs, vibration reports, pass/fail verdicts).
- DATASHEET: Supplier / OEM component electrical, thermal, or mechanical limits.
- COMPLIANCE_MATRIX: Verification matrix tracking requirement compliance status across standards.
- RISK_ASSESSMENT: Hazard analysis, FMEA, safety case.
- ARCHITECTURE: System architecture, interface definition, block diagrams.
- OTHER_EVIDENCE: General user manual, certificate, or other evidence.

For every document also return:
- confidence from 0 to 100;
- issuer, document status, verification basis, referenced standards, and subject when supported;
- 1-4 short classification_evidence quotes with their [PAGE n] numbers.

Classify from the page content and structure. The filename is only a weak hint.
Do not invent metadata that is not present. A TEST_REPORT may contain calculations;
its verification_basis remains physical_test when those calculations derive results
from measured physical testing.

Documents:
{"\n".join(docs_block)}
"""
    system_instruction = "Classify technical engineering documents into their exact audit role based on content and structure."

    try:
        result: Optional[DocumentClassificationResult] = await generate_structured(
            prompt=prompt,
            response_model=DocumentClassificationResult,
            model=active_model,
            system_instruction=system_instruction,
            thinking_level=thinking_level or "LOW",
        )
        if result and result.classifications:
            return {c.document_name: c for c in result.classifications}
    except Exception as ex:
        logger.warning(f"LLM document role classification failed: {ex}")

    return {}


async def profile_document_chunks(
    document_name: str,
    chunks: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> DocumentProfile:
    """Create one auditable content-derived profile for an arbitrary document."""
    cached = next(
        (
            chunk.get("document_profile")
            or (chunk.get("metadata") or {}).get("document_profile")
            for chunk in chunks
            if chunk.get("document_profile") or (chunk.get("metadata") or {}).get("document_profile")
        ),
        None,
    )
    if cached:
        try:
            return DocumentProfile.model_validate(cached)
        except Exception:
            logger.warning("Ignoring invalid cached document profile for %s", document_name)

    fallback = _heuristic_profile(document_name, chunks)
    llm_profiles = await classify_documents_with_llm(
        [{"filename": document_name, "excerpt": _profile_excerpt(chunks)}],
        model=model,
        thinking_level=thinking_level,
    )
    classification = llm_profiles.get(document_name)
    return _profile_from_classification(classification, fallback) if classification else fallback


async def profile_documents(
    documents: list[Any],
    all_chunks: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> dict[str, DocumentProfile]:
    """Profile a corpus once per document, reusing any profile cached on chunks."""
    profiles: dict[str, DocumentProfile] = {}
    missing: list[tuple[Any, list[dict[str, Any]], DocumentProfile]] = []
    excerpts: list[dict[str, str]] = []

    for document in documents:
        name = document.original_filename
        chunks = [item for item in all_chunks if item.get("document_id") == document.id]
        cached = next((item.get("document_profile") for item in chunks if item.get("document_profile")), None)
        if cached:
            try:
                profiles[document.id] = DocumentProfile.model_validate(cached)
                continue
            except Exception:
                logger.warning("Ignoring invalid cached document profile for %s", name)
        fallback = _heuristic_profile(name, chunks)
        missing.append((document, chunks, fallback))
        excerpts.append({"filename": name, "excerpt": _profile_excerpt(chunks)})

    llm_profiles = await classify_documents_with_llm(
        excerpts,
        model=model,
        thinking_level=thinking_level,
    ) if excerpts else {}

    for document, _chunks, fallback in missing:
        classification = llm_profiles.get(document.original_filename)
        profiles[document.id] = (
            _profile_from_classification(classification, fallback)
            if classification
            else fallback
        )
    return profiles


# ── Unified Specification Discovery ───────────────────────────────────────────

async def discover_specification_documents(
    documents: list[Any],
    all_chunks: list[dict],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    document_profiles: Optional[dict[str, DocumentProfile]] = None,
) -> tuple[list[Any], set[str]]:
    """Discover the primary specification documents from which requirements should be extracted.

    Returns:
        (spec_documents, spec_document_names_set)

    Works reliably on any filename convention (e.g. 'TechnicalSpec_V2.pdf', 'Design_Constraints.docx',
    'System_Requirements.xlsx', etc.) using content-based heuristic scoring and LLM classification.
    """
    if not documents:
        return [], set()

    if len(documents) == 1:
        doc = documents[0]
        return [doc], {doc.original_filename}

    if document_profiles:
        profiled_specs = [
            document
            for document in documents
            if document_profiles.get(document.id)
            and document_profiles[document.id].primary_role == "SPECIFICATION"
        ]
        if profiled_specs:
            names = {document.original_filename for document in profiled_specs}
            logger.info("Document profiles identified specification document(s): %s", names)
            return profiled_specs, names

    # Step 1: Gather text excerpts for each document
    doc_texts: dict[str, str] = {}
    doc_excerpts: list[dict[str, str]] = []
    for doc in documents:
        doc_chunks = [c["content"] for c in all_chunks if c.get("document_id") == doc.id]
        full_text = " ".join(doc_chunks)
        doc_texts[doc.id] = full_text
        doc_excerpts.append({
            "filename": doc.original_filename,
            "excerpt": full_text[:1200],
        })

    # Step 2: Run LLM classification if available
    llm_roles = await classify_documents_with_llm(
        doc_excerpts=doc_excerpts,
        model=model,
        thinking_level=thinking_level,
    )

    spec_docs = []
    for doc in documents:
        classification = llm_roles.get(doc.original_filename)
        if classification and classification.role == "SPECIFICATION":
            spec_docs.append(doc)

    if spec_docs:
        spec_names = {d.original_filename for d in spec_docs}
        logger.info(f"LLM identified {len(spec_docs)} specification document(s): {spec_names}")
        return spec_docs, spec_names

    # Step 3: Heuristic Content & Structure Scoring Fallback
    scored_docs = []
    for doc in documents:
        text = doc_texts.get(doc.id, "")
        scores = score_document_content(text, doc.original_filename)
        
        # A spec doc must have high SPECIFICATION score and significantly exceed TEST_REPORT / DATASHEET scores
        is_candidate_spec = (
            scores["SPECIFICATION"] > scores["TEST_REPORT"]
            and scores["SPECIFICATION"] > scores["DATASHEET"]
            and scores["SPECIFICATION"] >= 5.0
        )
        scored_docs.append((doc, scores, is_candidate_spec))

    # Filter documents where spec score dominated
    spec_docs = [doc for doc, scores, is_spec in scored_docs if is_spec]

    # If no clear winner, pick the document with the highest SPECIFICATION score
    if not spec_docs:
        sorted_by_spec = sorted(scored_docs, key=lambda item: item[1]["SPECIFICATION"], reverse=True)
        if sorted_by_spec:
            spec_docs = [sorted_by_spec[0][0]]

    # Absolute fallback
    if not spec_docs and documents:
        spec_docs = [documents[0]]

    spec_names = {d.original_filename for d in spec_docs}
    logger.info(f"Heuristic scoring identified specification document(s): {spec_names}")
    return spec_docs, spec_names
