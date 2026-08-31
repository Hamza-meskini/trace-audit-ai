"""Requirement extraction service.

Uses Google Gemini (e.g. gemini-3.7-flash, gemini-3.1-pro-preview) or OpenAI
with structured schema validation, falling back to a deterministic rule-based
parser when no API key is provided or when running offline.
"""

import re
import asyncio
from typing import Optional, Union, Any
from pydantic import BaseModel, Field, field_validator
from app.config import settings
from app.services.llm_client import generate_structured


class ExtractedParameter(BaseModel):
    name: str = Field(description="Parameter name, e.g. 'voltage', 'temperature', 'rating'")
    value: Optional[str] = Field(None, description="Exact value string, e.g. '18-32 V DC'")
    min_val: Optional[Union[float, str]] = Field(None, description="Minimum numeric value if applicable")
    max_val: Optional[Union[float, str]] = Field(None, description="Maximum numeric value if applicable")
    unit: Optional[str] = Field(None, description="Unit of measurement, e.g. 'V', '°C', 'kV'")

    @field_validator("min_val", "max_val", mode="before")
    @classmethod
    def parse_numeric(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            match = re.match(r"^\s*(?:[<>]=?\s*)?([+-]?\d+(?:\.\d+)?)", v)
            return float(match.group(1)) if match else None
        return None


class ExtractedCondition(BaseModel):
    """One mandatory, independently verifiable clause in a requirement."""

    condition_id: Optional[str] = None
    description: str
    parameter: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[Union[float, str, bool]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: Optional[str] = None
    mandatory: bool = True

    @field_validator("min_value", "max_value", mode="before")
    @classmethod
    def parse_numeric_bound(cls, value: Any) -> Optional[float]:
        """Discard misplaced identifiers instead of rejecting a whole batch."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.match(r"^\s*(?:[<>]=?\s*)?([+-]?\d+(?:\.\d+)?)", value)
            return float(match.group(1)) if match else None
        return None


class ExtractedClauseCoverage(BaseModel):
    """Mapping from an obligation-bearing source clause to atomic conditions."""

    clause: str = Field(description="Exact or minimally normalized obligation-bearing clause")
    condition_ids: list[str] = Field(
        default_factory=list,
        description="IDs of all atomic conditions that formalize this clause",
    )



class ExtractedRequirement(BaseModel):
    req_code: str = Field(description="Requirement identifier, e.g. REQ-001")
    title: str = Field(description="Short concise summary of requirement")
    description: Optional[str] = Field(None, description="Full requirement text")
    category: str = Field("General", description="Category: Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation")
    severity: str = Field("Medium", description="Severity: Critical, High, Medium, Low")
    parameters: list[ExtractedParameter] = Field(default_factory=list)
    conditions: list[ExtractedCondition] = Field(default_factory=list)
    clause_coverage: list[ExtractedClauseCoverage] = Field(default_factory=list)
    unmapped_obligations: list[str] = Field(default_factory=list)
    contract_complete: bool = Field(
        False,
        description="True only when every obligation-bearing clause maps to one or more conditions",
    )


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

    # Preserve a condition tree even in offline mode. The deterministic prose
    # parser is only a fallback; once produced, these conditions become the
    # canonical contract input for the rest of the pipeline.
    from app.schemas.contract import parse_requirement_contract
    for req in reqs:
        if req.conditions:
            continue
        contract = parse_requirement_contract(
            req_code=req.req_code,
            title=req.title,
            description=req.description,
            category=req.category,
        )
        req.conditions = [
            ExtractedCondition(
                condition_id=c.condition_id,
                description=c.description or c.condition_id,
                parameter=c.parameter,
                operator=c.operator,
                threshold=c.threshold,
                min_value=c.min_value,
                max_value=c.max_value,
                unit=c.unit,
                mandatory=c.mandatory,
            )
            for c in contract.atomic_conditions
        ]
        # The local prose parser can preserve a usable fallback condition tree,
        # but it cannot certify exhaustive semantic decomposition. Keep the
        # requirement auditable and explicitly disable automatic closure.
        req.unmapped_obligations = [req.description or req.title]
        req.contract_complete = False
    return reqs


# ── LLM-Powered Extraction ───────────────────────────────────────────────────

# Structured output, rather than prompt input, is the limiting factor for large
# specifications. Keep each request small enough that the JSON can finish within
# the model's output budget and split on requirement boundaries whenever IDs are
# present. Character windows remain a fallback for unstructured prose.
EXTRACTION_CHUNK_SIZE = 5000
EXTRACTION_CHUNK_OVERLAP = 500
EXTRACTION_REQUIREMENTS_PER_CHUNK = 5
EXTRACTION_MAX_RETRY_DEPTH = 2
EXTRACTION_MAX_OUTPUT_TOKENS = 8192
EXTRACTION_MAX_CONCURRENCY = 3

_REQUIREMENT_START_RE = re.compile(
    r"(?mi)^(?=[ \t]*(?:REQ[-_]?[A-Za-z0-9_-]*\d+|R[-_]?[A-Za-z0-9_-]*\d+)[ \t]*[:\-–—]?[ \t]*)"
)


def _requirement_blocks(text: str) -> list[str]:
    """Return complete requirement blocks when explicit IDs are available."""
    starts = [match.start() for match in _REQUIREMENT_START_RE.finditer(text)]
    if not starts:
        return []

    blocks: list[str] = []
    preamble = text[:starts[0]].strip()
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        block = text[start:end].strip()
        if not block:
            continue
        if preamble and not blocks:
            block = f"{preamble}\n{block}"
        blocks.append(block)
    return blocks


def _requirement_code_from_block(block: str) -> Optional[str]:
    match = re.match(
        r"^[ \t]*(REQ[-_]?[A-Za-z0-9_-]*\d+|R[-_]?[A-Za-z0-9_-]*\d+)",
        block,
        re.IGNORECASE,
    )
    return match.group(1).upper().replace("_", "-") if match else None


def _deduplicate_requirements(
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    unique: list[ExtractedRequirement] = []
    seen: set[str] = set()
    for requirement in requirements:
        if requirement.req_code not in seen:
            seen.add(requirement.req_code)
            unique.append(requirement)
    return unique


def _split_text_into_chunks(
    text: str,
    chunk_size: int = EXTRACTION_CHUNK_SIZE,
    overlap: int = EXTRACTION_CHUNK_OVERLAP,
    max_requirements_per_chunk: int = EXTRACTION_REQUIREMENTS_PER_CHUNK,
) -> list[str]:
    """Split text without cutting explicit requirement clauses in half."""
    blocks = _requirement_blocks(text)
    if blocks:
        chunks: list[str] = []
        current: list[str] = []
        current_chars = 0
        for block in blocks:
            exceeds_count = len(current) >= max_requirements_per_chunk
            exceeds_chars = bool(current) and current_chars + len(block) + 1 > chunk_size
            if exceeds_count or exceeds_chars:
                chunks.append("\n".join(current))
                current = []
                current_chars = 0
            current.append(block)
            current_chars += len(block) + 1
        if current:
            chunks.append("\n".join(current))
        return chunks

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        # Prefer a paragraph/newline boundary for prose-only documents.
        if end < len(text):
            boundary = text.rfind("\n", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _normalize_extracted_requirements(
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    """Normalize stable IDs while preserving the model's semantic contract."""
    for req in requirements:
        req.req_code = req.req_code.strip().upper().replace("_", "-")
        for index, condition in enumerate(req.conditions, 1):
            if not condition.condition_id:
                condition.condition_id = f"{req.req_code}-C{index}"
            if not condition.description.strip():
                condition.description = condition.parameter or req.title

        condition_ids = {
            condition.condition_id
            for condition in req.conditions
            if condition.condition_id
        }
        mandatory_ids = {
            condition.condition_id
            for condition in req.conditions
            if condition.mandatory and condition.condition_id
        }
        covered_ids: set[str] = set()
        invalid_mapping = False
        normalized_unmapped = [
            obligation.strip()
            for obligation in req.unmapped_obligations
            if obligation and obligation.strip()
        ]

        for mapping in req.clause_coverage:
            mapping.clause = mapping.clause.strip()
            supplied_ids = list(dict.fromkeys(
                condition_id.strip()
                for condition_id in mapping.condition_ids
                if condition_id and condition_id.strip()
            ))
            valid_ids = [condition_id for condition_id in supplied_ids if condition_id in condition_ids]
            mapping.condition_ids = valid_ids
            covered_ids.update(valid_ids)
            if not mapping.clause or not valid_ids or len(valid_ids) != len(supplied_ids):
                invalid_mapping = True
                if mapping.clause:
                    normalized_unmapped.append(mapping.clause)

        req.unmapped_obligations = list(dict.fromkeys(normalized_unmapped))
        internally_complete = (
            bool(req.conditions)
            and bool(req.clause_coverage)
            and not invalid_mapping
            and not req.unmapped_obligations
            and mandatory_ids.issubset(covered_ids)
        )
        # Never promote an uncertain model result to complete. Python only
        # rejects internally inconsistent completeness claims.
        req.contract_complete = bool(req.contract_complete and internally_complete)
    return requirements


def _build_extraction_prompt(chunk_text: str, doc_name: str, chunk_label: str) -> str:
    return f"""You are an engineering requirements auditor for manufacturing and industrial hardware/software.
Extract all technical requirements, design constraints, performance criteria, and testable specifications from the following document excerpt.

Document: {doc_name} {chunk_label}
Text:
{chunk_text}

For each requirement, provide:
- req_code (preserve the exact existing ID when one is present)
- title (concise summary)
- description (the complete requirement clause, without dropping joined clauses)
- category (Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation)
- severity (Critical, High, Medium, Low)
- parameters (numeric values, min/max limits, units like V, °C, kV, IP rating, MTBF hours)
- conditions (one entry per independently verifiable clause, with a stable condition_id,
  description, parameter, operator, threshold/min/max, unit, and mandatory flag)
- clause_coverage (one entry for every obligation-bearing clause in the description;
  copy the clause and list every condition_id that formalizes it)
- unmapped_obligations (obligation-bearing clauses that could not be formalized; use [] only when none exist)
- contract_complete (true only when every obligation-bearing clause is represented by one or more conditions,
  every mandatory condition is referenced by clause_coverage, and unmapped_obligations is empty)

Treat required actions, triggers/preconditions, required outcomes, interfaces, operating modes,
timing limits, quantities, ranges, tolerances, and verification-method constraints as separate
conditions whenever each can independently pass or fail. A shared timing limit does not replace
the actions or outcomes that must occur within that time.
"""


async def _extract_chunk_with_retry(
    chunk_text: str,
    *,
    doc_name: str,
    active_model: str,
    thinking_level: Optional[str],
    chunk_label: str,
    depth: int = 0,
) -> list[ExtractedRequirement]:
    """Extract one bounded chunk, recursively splitting invalid/truncated JSON."""
    system_instruction = (
        "You extract structured engineering requirements accurately. Preserve every joined clause as a separate "
        "atomic condition; never merge values with different units or omit a pending verification dimension. "
        "Build an exhaustive clause-to-condition coverage map. Mark contract_complete false whenever an "
        "obligation is unmapped, a mapping is uncertain, or a required action/outcome is represented only by "
        "a shared numeric timing condition. "
        "Return only complete JSON; never stop part-way through a requirement."
    )
    result: Optional[ExtractionResult] = None
    try:
        result = await generate_structured(
            prompt=_build_extraction_prompt(chunk_text, doc_name, chunk_label),
            response_model=ExtractionResult,
            model=active_model,
            system_instruction=system_instruction,
            thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
            max_output_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
        )
    except Exception as ex:
        import logging
        logging.getLogger("traceaudit.extraction").warning(
            "LLM extraction raised for %s at retry depth %s: %s",
            chunk_label,
            depth,
            ex,
        )

    blocks = _requirement_blocks(chunk_text)
    if result and result.requirements:
        normalized = _normalize_extracted_requirements(result.requirements)
        expected_by_code = {
            code: block
            for block in blocks
            if (code := _requirement_code_from_block(block)) is not None
        }
        returned_codes = {requirement.req_code for requirement in normalized}
        missing_blocks = [
            block for code, block in expected_by_code.items() if code not in returned_codes
        ]
        if not missing_blocks:
            return normalized

        print(
            f"    [Extraction retry] {chunk_label} returned valid but incomplete JSON; "
            f"recovering {len(missing_blocks)} omitted requirement(s).",
            flush=True,
        )
        if depth < EXTRACTION_MAX_RETRY_DEPTH:
            recovered = await _extract_chunk_with_retry(
                "\n".join(missing_blocks),
                doc_name=doc_name,
                active_model=active_model,
                thinking_level=thinking_level,
                chunk_label=f"{chunk_label}.missing",
                depth=depth + 1,
            )
        else:
            recovered = _normalize_extracted_requirements(
                fallback_extract_requirements("\n".join(missing_blocks), doc_name)
            )
        return _deduplicate_requirements(normalized + recovered)

    if depth < EXTRACTION_MAX_RETRY_DEPTH and len(blocks) > 1:
        midpoint = (len(blocks) + 1) // 2
        retry_groups = [blocks[:midpoint], blocks[midpoint:]]
        recovered: list[ExtractedRequirement] = []
        print(
            f"    [Extraction retry] Invalid or truncated output for {chunk_label}; "
            f"retrying as {len(retry_groups)} smaller sections.",
            flush=True,
        )
        for retry_index, group in enumerate(retry_groups, 1):
            if not group:
                continue
            recovered.extend(
                await _extract_chunk_with_retry(
                    "\n".join(group),
                    doc_name=doc_name,
                    active_model=active_model,
                    thinking_level=thinking_level,
                    chunk_label=f"{chunk_label}.{retry_index}",
                    depth=depth + 1,
                )
            )
        return recovered

    fallback = fallback_extract_requirements(chunk_text, doc_name)
    print(
        f"    [Extraction fallback] {chunk_label} could not be parsed by the LLM; "
        f"recovered {len(fallback)} requirement(s) deterministically.",
        flush=True,
    )
    return _normalize_extracted_requirements(fallback)


async def extract_requirements_from_text(
    text: str,
    doc_name: str = "",
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> list[ExtractedRequirement]:
    """Extract structured requirements from document text using Gemini (with Thinking enabled) or Databricks/OpenAI.

    For large documents, explicit requirement boundaries are preserved and each
    bounded group is processed independently. Invalid/truncated output is retried
    on smaller groups before a local deterministic fallback is used.
    """
    active_model = model or settings.LLM_MODEL
    has_keys = bool(settings.effective_gemini_api_key or settings.effective_databricks_token or settings.effective_openai_api_key)

    if not has_keys:
        return fallback_extract_requirements(text, doc_name)

    text_chunks = _split_text_into_chunks(text)
    print(f"  • Extracting requirements across {len(text_chunks)} document sections...", flush=True)

    semaphore = asyncio.Semaphore(EXTRACTION_MAX_CONCURRENCY)

    async def process_chunk(
        chunk_idx: int,
        chunk_text: str,
    ) -> tuple[int, list[ExtractedRequirement]]:
        chunk_label = f"(Section {chunk_idx + 1}/{len(text_chunks)})" if len(text_chunks) > 1 else ""
        async with semaphore:
            print(f"    [Extraction {chunk_idx + 1:02d}/{len(text_chunks):02d}] Processing section {chunk_idx + 1} ({len(chunk_text)} chars)...", flush=True)
            requirements = await _extract_chunk_with_retry(
                chunk_text,
                doc_name=doc_name,
                active_model=active_model,
                thinking_level=thinking_level,
                chunk_label=chunk_label or "Section 1/1",
            )
        return chunk_idx, requirements

    chunk_results = await asyncio.gather(*(
        process_chunk(chunk_idx, chunk_text)
        for chunk_idx, chunk_text in enumerate(text_chunks)
    ))

    all_requirements: list[ExtractedRequirement] = []
    seen_codes: set[str] = set()
    for chunk_idx, requirements in sorted(chunk_results, key=lambda item: item[0]):
        added_count = 0
        for req in requirements:
            # Deduplicate by normalized req_code across bounded/retried chunks.
            if req.req_code not in seen_codes:
                seen_codes.add(req.req_code)
                all_requirements.append(req)
                added_count += 1
        print(f"    [Extraction {chunk_idx + 1:02d}/{len(text_chunks):02d}] Extracted {added_count} new requirements (Total unique: {len(all_requirements)})", flush=True)

    if all_requirements:
        return all_requirements

    return fallback_extract_requirements(text, doc_name)
