"""Requirement extraction service.

Uses Google Gemini (e.g. gemini-3.7-flash, gemini-3.1-pro-preview) or OpenAI
with structured schema validation, falling back to a deterministic rule-based
parser when no API key is provided or when running offline.
"""

import re
from typing import Optional
from pydantic import BaseModel, Field
from app.config import settings
from app.services.llm_client import generate_structured


class ExtractedParameter(BaseModel):
    name: str = Field(description="Parameter name, e.g. 'voltage', 'temperature', 'rating'")
    value: Optional[str] = Field(None, description="Exact value string, e.g. '18-32 V DC'")
    min_val: Optional[float] = Field(None, description="Minimum numeric value if applicable")
    max_val: Optional[float] = Field(None, description="Maximum numeric value if applicable")
    unit: Optional[str] = Field(None, description="Unit of measurement, e.g. 'V', '°C', 'kV'")


class ExtractedRequirement(BaseModel):
    req_code: str = Field(description="Requirement identifier, e.g. REQ-001")
    title: str = Field(description="Short concise summary of requirement")
    description: Optional[str] = Field(None, description="Full requirement text")
    category: str = Field("General", description="Category: Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation")
    severity: str = Field("Medium", description="Severity: Critical, High, Medium, Low")
    parameters: list[ExtractedParameter] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


# ── Deterministic Rule-Based Fallback ─────────────────────────────────────────

CATEGORY_KEYWORDS = {
    "Electrical": ["voltage", "current", "power", "surge", "v dc", "v ac", "supply", "tolerance", "grounding", "frequency"],
    "Safety": ["safety", "over-voltage", "hazard", "risk", "protection", "clamping", "insulation", "emergency"],
    "Environmental": ["temperature", "thermal", "humidity", "vibration", "shock", "operating range", "cooling", "ambient"],
    "Mechanical": ["enclosure", "ip54", "ip65", "ip67", "ingress", "mounting", "chassis", "dimensions", "weight", "housing"],
    "Cybersecurity": ["firmware", "cryptographic", "signed", "authentication", "port", "diagnostic", "encryption", "tls", "security"],
    "Documentation": ["manual", "instructions", "mtbf", "datasheet", "specification", "archived", "declaration", "certificate"],
}

NUMERIC_RANGE_REGEX = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?", re.IGNORECASE)


def infer_category(text: str) -> str:
    text_lower = text.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            return cat
    return "Documentation"


def extract_parameters_from_text(text: str) -> list[ExtractedParameter]:
    params = []
    # Check for range: e.g. 18–32 V DC, -20°C to +70°C, 400.0 V to 800.0 V DC
    for match in NUMERIC_RANGE_REGEX.finditer(text):
        min_v = match.group(1)
        unit_pre = match.group(2)
        max_v = match.group(3)
        unit_post = match.group(4)
        unit = (unit_post or unit_pre or "").strip()
        try:
            params.append(ExtractedParameter(
                name="operating_range",
                value=match.group(0),
                min_val=float(min_v),
                max_val=float(max_v),
                unit=unit if unit else None,
            ))
        except (ValueError, TypeError):
            pass

    # Check for ingress protection, e.g. IP54
    if "ip" in text.lower():
        ip_match = re.search(r"IP\d{2}", text, re.IGNORECASE)
        if ip_match:
            params.append(ExtractedParameter(
                name="ingress_protection",
                value=ip_match.group(0).upper(),
                unit="IP",
            ))

    return params


def fallback_extract_requirements(text_content: str, doc_name: str = "") -> list[ExtractedRequirement]:
    """Parse text lines and identify requirements using heuristic rules."""
    reqs: list[ExtractedRequirement] = []
    lines = text_content.splitlines()
    req_counter = 1

    # First pass: check if document has explicit requirement codes (e.g. REQ-BCU-001, REQ-001)
    has_explicit_codes = any(
        re.match(r"^(REQ[-_]?[A-Za-z0-9_-]*\d+|R[-_]?[A-Za-z0-9_-]*\d+)\s*[:\-–]?\s*", l.strip(), re.IGNORECASE)
        for l in lines if len(l.strip()) >= 10
    )

    for line in lines:
        cleaned = line.strip()
        if not cleaned or len(cleaned) < 15:
            continue

        # Check for explicit code e.g. "REQ-001: ...", "REQ-BCU-001: ...", "REQ_ELE_001: ..."
        req_match = re.match(r"^(REQ[-_]?[A-Za-z0-9_-]*\d+|R[-_]?[A-Za-z0-9_-]*\d+)\s*[:\-–]?\s*(.*)", cleaned, re.IGNORECASE)
        if req_match:
            code = req_match.group(1).upper().replace("_", "-")
            title = req_match.group(2).strip() or cleaned
            cat = infer_category(title)
            params = extract_parameters_from_text(title)
            reqs.append(ExtractedRequirement(
                req_code=code,
                title=title,
                description=cleaned,
                category=cat,
                severity="High" if cat in ("Safety", "Electrical") else "Medium",
                parameters=params,
            ))
        elif not has_explicit_codes and any(verb in cleaned.lower() for verb in [" shall ", " must ", " required to ", " operates between ", " operating range"]):
            # Only synthesize numbered REQ-00x codes if document has NO explicit requirement codes at all
            if reqs and (reqs[-1].description == reqs[-1].title or len(reqs[-1].description or "") < 80):
                reqs[-1].description = f"{reqs[-1].title}. {cleaned}"
                if not reqs[-1].parameters:
                    reqs[-1].parameters = extract_parameters_from_text(cleaned)
                continue

            code = f"REQ-{req_counter:03d}"
            req_counter += 1
            cat = infer_category(cleaned)
            params = extract_parameters_from_text(cleaned)
            reqs.append(ExtractedRequirement(
                req_code=code,
                title=cleaned[:120],
                description=cleaned,
                category=cat,
                severity="High" if cat in ("Safety", "Electrical") else "Medium",
                parameters=params,
            ))

    return reqs


# ── LLM-Powered Extraction ───────────────────────────────────────────────────

# Chunk size for splitting large documents before sending to the LLM.
# Each chunk is sent as a separate extraction call; results are deduplicated.
EXTRACTION_CHUNK_SIZE = 8000
EXTRACTION_CHUNK_OVERLAP = 500


def _split_text_into_chunks(text: str, chunk_size: int = EXTRACTION_CHUNK_SIZE, overlap: int = EXTRACTION_CHUNK_OVERLAP) -> list[str]:
    """Split document text into overlapping windows for iterative extraction."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap  # Overlap to avoid splitting a requirement across chunk boundaries
    return chunks


async def extract_requirements_from_text(
    text: str,
    doc_name: str = "",
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> list[ExtractedRequirement]:
    """Extract structured requirements from document text using Gemini (with Thinking enabled) or Databricks/OpenAI.

    For large documents, the text is split into overlapping chunks and each chunk
    is processed independently. Results are deduplicated by requirement code to
    avoid duplicates from the overlap regions.
    """
    active_model = model or settings.LLM_MODEL
    has_keys = bool(settings.effective_gemini_api_key or settings.effective_databricks_token or settings.effective_openai_api_key)

    if not has_keys:
        return fallback_extract_requirements(text, doc_name)

    text_chunks = _split_text_into_chunks(text)
    all_requirements: list[ExtractedRequirement] = []
    seen_codes: set[str] = set()

    for chunk_idx, chunk_text in enumerate(text_chunks):
        chunk_label = f"(Section {chunk_idx + 1}/{len(text_chunks)})" if len(text_chunks) > 1 else ""
        prompt = f"""You are an engineering requirements auditor for manufacturing and industrial hardware/software.
Extract all technical requirements, design constraints, performance criteria, and testable specifications from the following document excerpt.

Document: {doc_name} {chunk_label}
Text:
{chunk_text}

For each requirement, provide:
- req_code (e.g. REQ-001, or existing ID if present in text)
- title (concise summary)
- description (full clause)
- category (Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation)
- severity (Critical, High, Medium, Low)
- parameters (numeric values, min/max limits, units like V, °C, kV, IP rating, MTBF hours)
"""
        system_instruction = "You extract structured engineering requirements accurately with precise numeric parameters."

        try:
            result: Optional[ExtractionResult] = await generate_structured(
                prompt=prompt,
                response_model=ExtractionResult,
                model=active_model,
                system_instruction=system_instruction,
                thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
            )

            if result and result.requirements:
                for req in result.requirements:
                    # Deduplicate by req_code across overlapping chunks
                    if req.req_code not in seen_codes:
                        seen_codes.add(req.req_code)
                        all_requirements.append(req)
        except Exception as ex:
            import logging
            logging.getLogger("traceaudit.extraction").warning(
                f"LLM extraction failed for chunk {chunk_idx + 1}/{len(text_chunks)} of {doc_name}: {ex}"
            )

    if all_requirements:
        return all_requirements

    return fallback_extract_requirements(text, doc_name)
