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

NUMERIC_RANGE_REGEX = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(?:–|-|to)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/]+)?", re.IGNORECASE)


def infer_category(text: str) -> str:
    text_lower = text.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            return cat
    return "Documentation"


def extract_parameters_from_text(text: str) -> list[ExtractedParameter]:
    params = []
    # Check for range: e.g. 18–32 V DC, -20°C to +70°C
    for match in NUMERIC_RANGE_REGEX.finditer(text):
        min_v, max_v, unit = match.groups()
        try:
            params.append(ExtractedParameter(
                name="operating_range",
                value=match.group(0),
                min_val=float(min_v),
                max_val=float(max_v),
                unit=unit.strip() if unit else None,
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

    for line in lines:
        cleaned = line.strip()
        if not cleaned or len(cleaned) < 15:
            continue

        # Check for explicit code e.g. "REQ-001: ..."
        req_match = re.match(r"^(REQ[-_]?\d+|R[-_]?\d+)\s*[:\-–]?\s*(.*)", cleaned, re.IGNORECASE)
        if req_match:
            code = req_match.group(1).upper()
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
        elif any(verb in cleaned.lower() for verb in [" shall ", " must ", " required to ", " operates between ", " operating range"]):
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

async def extract_requirements_from_text(
    text: str,
    doc_name: str = "",
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> list[ExtractedRequirement]:
    """Extract structured requirements from document text using Gemini (with Thinking enabled) or OpenAI."""
    active_model = model or settings.LLM_MODEL
    has_keys = bool(settings.effective_gemini_api_key or settings.effective_openai_api_key)

    if not has_keys:
        return fallback_extract_requirements(text, doc_name)

    prompt = f"""You are an engineering requirements auditor for manufacturing and industrial hardware/software.
Extract all technical requirements, design constraints, performance criteria, and testable specifications from the following document excerpt.

Document: {doc_name}
Text:
{text[:9000]}

For each requirement, provide:
- req_code (e.g. REQ-001, or existing ID if present in text)
- title (concise summary)
- description (full clause)
- category (Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation)
- severity (Critical, High, Medium, Low)
- parameters (numeric values, min/max limits, units like V, °C, kV, IP rating, MTBF hours)
"""
    system_instruction = "You extract structured engineering requirements accurately with precise numeric parameters."

    result: Optional[ExtractionResult] = await generate_structured(
        prompt=prompt,
        response_model=ExtractionResult,
        model=active_model,
        system_instruction=system_instruction,
        thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
    )

    if result and result.requirements:
        return result.requirements

    return fallback_extract_requirements(text, doc_name)
