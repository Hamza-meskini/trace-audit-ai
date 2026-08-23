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


class ClassifiedDocument(BaseModel):
    document_name: str
    role: DocumentRoleType
    confidence: float = Field(default=80.0, description="Confidence score 0-100")
    reason: str = Field(default="", description="Brief explanation of document role")


class DocumentClassificationResult(BaseModel):
    classifications: list[ClassifiedDocument] = Field(default_factory=list)


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
    r"\bverdict\s*:\s*(?:pass|fail)\b",
    r"\btest\s+result[s]?\s*:\s*(?:pass|fail|passed|failed)\b",
    r"\bmeasured\s+value[s]?\b",
    r"\btest\s+procedure\b",
    r"\blab\s+(?:report|testing|measurements)\b",
    r"\btest\s+setup\b",
    r"\bexecuted\s+on\b",
    r"\bserial\s+number\b",
    r"\btest\s+verdict\b",
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


def score_document_content(text: str, filename: str) -> dict[str, float]:
    """Score document text across technical roles using regex density analysis."""
    text_lower = text.lower()
    fn_lower = filename.lower()

    # Spec score: boosted by normative keywords and requirement IDs
    spec_matches = sum(len(re.findall(pat, text_lower)) for pat in NORMATIVE_SPEC_MARKERS)
    spec_score = spec_matches * 1.5
    if any(k in fn_lower for k in ("spec", "requirement", "srs", "prd", "prs", "constraint", "system_definition")):
        spec_score += 10.0

    # Test report score: boosted by test verdicts and lab markers
    test_matches = sum(len(re.findall(pat, text_lower)) for pat in TEST_VERDICT_MARKERS)
    test_score = test_matches * 2.0
    if any(k in fn_lower for k in ("test", "report", "lab", "validation", "verification_report", "tr-")):
        test_score += 10.0

    # Datasheet score: boosted by component ratings
    ds_matches = sum(len(re.findall(pat, text_lower)) for pat in DATASHEET_MARKERS)
    ds_score = ds_matches * 2.5
    if any(k in fn_lower for k in ("datasheet", "ds-", "supplier", "oem", "component", "ics-")):
        ds_score += 10.0

    # Matrix score
    matrix_matches = sum(len(re.findall(pat, text_lower)) for pat in COMPLIANCE_MATRIX_MARKERS)
    matrix_score = matrix_matches * 3.0
    if any(k in fn_lower for k in ("matrix", "compliance_verification", "vcvm", "rvtm")):
        matrix_score += 12.0

    return {
        "SPECIFICATION": spec_score,
        "TEST_REPORT": test_score,
        "DATASHEET": ds_score,
        "COMPLIANCE_MATRIX": matrix_score,
    }


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
        excerpt = (d.get("excerpt", "")[:1200]).strip()
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


# ── Unified Specification Discovery ───────────────────────────────────────────

async def discover_specification_documents(
    documents: list[Any],
    all_chunks: list[dict],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
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
