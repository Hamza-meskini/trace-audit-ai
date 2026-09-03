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

    @field_validator("value", mode="before")
    @classmethod
    def coerce_scalar_value(cls, value: Any) -> Optional[str]:
        """Accept JSON numeric scalars without rejecting an otherwise valid extraction."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            cleaned = str(value).strip()
            return cleaned or None
        return None

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
    condition_role: str = Field(
        "VERIFICATION",
        pattern="^(VERIFICATION|APPLICABILITY)$",
        description="APPLICABILITY only for a trigger/precondition; VERIFICATION for an auditable obligation",
    )
    description: str
    parameter: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[Union[float, str, bool]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: Optional[str] = None
    mandatory: bool = True
    requires_visual_evidence: bool = Field(
        False,
        description="True when the condition can only be established from a figure, image, plot, or diagram",
    )

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


class ExtractedRequirementLogic(BaseModel):
    """Boolean relationship between the extracted atomic conditions."""

    operator: str = Field("ALL_OF", pattern="^(ALL_OF|ANY_OF|IF_THEN)$")
    condition_ids: list[str] = Field(default_factory=list)
    if_condition_id: Optional[str] = None
    then_condition_ids: list[str] = Field(default_factory=list)



class ExtractedRequirement(BaseModel):
    req_code: str = Field(description="Requirement identifier, e.g. REQ-001")
    title: str = Field(description="Short concise summary of requirement")
    description: Optional[str] = Field(None, description="Full requirement text")
    category: str = Field("General", description="Category: Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation")
    severity: str = Field("Medium", description="Severity: Critical, High, Medium, Low")
    parameters: list[ExtractedParameter] = Field(default_factory=list)
    conditions: list[ExtractedCondition] = Field(default_factory=list)
    logic: ExtractedRequirementLogic = Field(default_factory=ExtractedRequirementLogic)
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
                condition_role=c.condition_role,
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

# Regulatory standards use hierarchical clause identifiers rather than
# project-style REQ IDs. Match only heading-shaped lines: wrapped prose such as
# ``S5.3 when it is impacted`` starts with a lowercase continuation and is not
# a new block, while ``S7.6.6 If V1 ...`` is.
_REGULATORY_START_RE = re.compile(
    r"(?mi)^(?=[ \t]*S\d+(?:\.\d+)*(?:\([a-z0-9]+\))*[ \t]+(?:[A-Z§\"“]|$))"
)
# Some tagged PDFs expose a lettered subparagraph only in the repeated section
# path (for example ``SECTION: ... S7.1(c)``) while the body begins with
# ``(c)``. Restrict this recovery to identifiers containing parentheses so
# ordinary repeated parent-clause headers do not fragment the document.
_REGULATORY_SECTION_START_RE = re.compile(
    r"(?mi)^(?=SECTION:[^\n]*\bS\d+(?:\.\d+)*\(([a-z0-9]+)\)[^\n]*\n"
    r"(?:(?!SECTION:)[^\n]*\n){0,4}[ \t]*\(\1\))"
)
_REGULATORY_CODE_RE = re.compile(
    r"^[ \t]*(S\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)(?=\s|[.:\-–—]|$)",
    re.IGNORECASE,
)
_REGULATORY_SECTION_CODE_RE = re.compile(
    r"(?i)^SECTION:[^\n]*\b(S\d+(?:\.\d+)*\([a-z0-9]+\))(?=\s|$)"
)


def _requirement_blocks(text: str) -> list[str]:
    """Return complete requirement blocks when explicit IDs are available."""
    starts = sorted({
        *[match.start() for match in _REQUIREMENT_START_RE.finditer(text)],
        *[match.start() for match in _REGULATORY_START_RE.finditer(text)],
        *[match.start() for match in _REGULATORY_SECTION_START_RE.finditer(text)],
    })
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
    if match:
        return match.group(1).upper().replace("_", "-")
    regulatory = _REGULATORY_CODE_RE.match(block) or _REGULATORY_SECTION_CODE_RE.match(block)
    if not regulatory:
        return None
    code = regulatory.group(1).upper()
    # Preserve lower-case CFR subparagraph notation for readable, stable IDs.
    return re.sub(r"\(([A-Z0-9]+)\)", lambda item: f"({item.group(1).lower()})", code)


_ATOMIC_OBLIGATION_SIGNALS = (
    r"\bmeet(?:s|ing)?\b[^.;]*(?:requirements?|limits?)",
    r"\b(?:impact|impacted|subjected)\b",
    r"\bconform(?:s|ing|ed)?\b",
    r"\b(?:speed|velocity)\b[^.;]*(?:up to|not more|less than|including|≤|<=)",
    r"\b(?:dummy|dummies)\b",
    r"\b(?:insert|measure|calculate|divide|record|document)\b",
    r"\b(?:present|located)\b[^.;]*(?:on|near)\b",
    r"\bvisible\b",
    r"\b(?:yellow|black border|black arrow)\b",
    r"\b(?:remain attached|not enter|shall not enter)\b",
    r"\b(?:no visible|not more than)\b",
    r"\b(?:throughout|until\s+\d|from the time)\b",
)


def _estimated_atomic_obligations(source: str) -> int:
    """Conservative lower bound used only to request a second LLM pass.

    It never creates conditions or marks a contract complete. Its sole purpose
    is to catch obviously compressed clauses such as ``insert, measure,
    calculate, and divide`` before accepting the first extraction.
    """
    normalized = " ".join((source or "").split()).lower()
    if not normalized:
        return 0
    hits = sum(bool(re.search(pattern, normalized, re.IGNORECASE)) for pattern in _ATOMIC_OBLIGATION_SIGNALS)
    procedural_actions = set(re.findall(
        r"\b(insert|measure|calculate|divide|record|document)\b",
        normalized,
    ))
    if procedural_actions:
        hits = max(hits, len(procedural_actions))
    if re.search(r"\bif\b.+\b(?:shall|must|is|are|insert|measure|prevent)\b", normalized):
        hits = max(hits, len(procedural_actions) + 1, 2)
    if re.search(r"\bone of (?:the )?following\b|\beither\b", normalized):
        hits = max(hits, 2)
    return max(1, hits)


def _needs_completeness_retry(requirement: ExtractedRequirement, source: str) -> bool:
    return (
        requirement.contract_complete is False
        or len(requirement.conditions) < _estimated_atomic_obligations(source)
        or (
            requirement.logic.operator == "IF_THEN"
            and (not requirement.logic.if_condition_id or not requirement.logic.then_condition_ids)
        )
    )


def _deduplicate_requirements(
    requirements: list[ExtractedRequirement],
) -> list[ExtractedRequirement]:
    unique: list[ExtractedRequirement] = []
    positions: dict[str, int] = {}

    def quality(item: ExtractedRequirement) -> tuple[int, int, int, int]:
        return (
            int(item.contract_complete),
            len(item.conditions),
            len(item.clause_coverage),
            len(item.description or ""),
        )

    for requirement in requirements:
        position = positions.get(requirement.req_code)
        if position is None:
            positions[requirement.req_code] = len(unique)
            unique.append(requirement)
        elif quality(requirement) > quality(unique[position]):
            unique[position] = requirement
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
        known_ids = {condition.condition_id for condition in req.conditions if condition.condition_id}
        supplied_logic_ids = list(req.logic.condition_ids)
        supplied_then_ids = list(req.logic.then_condition_ids)
        supplied_if_id = req.logic.if_condition_id
        req.logic.condition_ids = [item for item in req.logic.condition_ids if item in known_ids]
        req.logic.then_condition_ids = [item for item in req.logic.then_condition_ids if item in known_ids]
        if req.logic.if_condition_id not in known_ids:
            req.logic.if_condition_id = None
        if not req.logic.condition_ids:
            req.logic.condition_ids = [condition.condition_id for condition in req.conditions if condition.condition_id]
        if req.logic.operator == "IF_THEN" and req.logic.if_condition_id:
            for condition in req.conditions:
                if condition.condition_id == req.logic.if_condition_id:
                    condition.condition_role = "APPLICABILITY"
        elif supplied_logic_ids:
            governed = set(req.logic.condition_ids)
            for condition in req.conditions:
                if condition.condition_id not in governed:
                    condition.condition_role = "APPLICABILITY"
        invalid_logic = (
            (bool(supplied_logic_ids) and len(req.logic.condition_ids) != len(supplied_logic_ids))
            or (bool(supplied_then_ids) and len(req.logic.then_condition_ids) != len(supplied_then_ids))
            or (supplied_if_id is not None and req.logic.if_condition_id is None)
            or (
                req.logic.operator == "IF_THEN"
                and (not req.logic.if_condition_id or not req.logic.then_condition_ids)
            )
        )

        condition_ids = {
            condition.condition_id
            for condition in req.conditions
            if condition.condition_id
        }
        mandatory_ids = {
            condition.condition_id
            for condition in req.conditions
            if (
                condition.mandatory
                and condition.condition_role == "VERIFICATION"
                and condition.condition_id
            )
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
            and not invalid_logic
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
  condition_role, description, parameter, operator, threshold/min/max, unit, mandatory flag, and
  requires_visual_evidence=true only when a figure/image/plot must be inspected)
- logic (operator ALL_OF when every listed condition is required; ANY_OF when satisfying any
  alternative path is sufficient; IF_THEN with one if_condition_id and all then_condition_ids
  for a conditional obligation; condition_ids must name every condition governed by the logic)
- clause_coverage (one entry for every obligation-bearing clause in the description;
  copy the clause and list every condition_id that formalizes it)
- unmapped_obligations (obligation-bearing clauses that could not be formalized; use [] only when none exist)
- contract_complete (true only when every obligation-bearing clause is represented by one or more conditions,
  every mandatory condition is referenced by clause_coverage, and unmapped_obligations is empty)

For standards with hierarchical identifiers, preserve the complete clause/subparagraph ID. For example,
extract lettered paragraph `(c)` under `S7.1` as `S7.1(c)`, not as `S7.1` and not as a generated REQ number.
Treat referenced figures/tables/formulas and numbered or lettered subparagraphs as part of the surrounding
requirement context. Do not discard a condition merely because its details are carried by a figure or table;
mark it `requires_visual_evidence=true` when visual inspection is necessary.

Treat required actions, required outcomes, interfaces, operating modes, timing limits, quantities,
ranges, tolerances, and verification-method constraints as separate VERIFICATION conditions whenever
each can independently pass or fail. Represent a trigger, precondition, or contextual gate as a separate
APPLICABILITY condition; do not count it as a verification obligation. For IF_THEN, if_condition_id must
name the APPLICABILITY condition and then_condition_ids must name every VERIFICATION obligation. For
ANY_OF, condition_ids must contain only alternative VERIFICATION paths; contextual phrases such as
"after impact" may be APPLICABILITY conditions but must not be included as alternatives. A shared timing
limit does not replace the actions or outcomes that must occur within that time.
"""


async def _retry_incomplete_contracts(
    source_text: str,
    requirements: list[ExtractedRequirement],
    *,
    doc_name: str,
    active_model: str,
    thinking_level: Optional[str],
    chunk_label: str,
) -> list[ExtractedRequirement]:
    """Ask once for corrected contracts when the first pass compressed clauses."""
    source_by_code = {
        code: block
        for block in _requirement_blocks(source_text)
        if (code := _requirement_code_from_block(block)) is not None
    }
    suspect = [
        requirement
        for requirement in requirements
        if _needs_completeness_retry(
            requirement,
            source_by_code.get(requirement.req_code, requirement.description or source_text),
        )
    ]
    if not suspect:
        return requirements

    prior = [item.model_dump(exclude_none=True) for item in suspect]
    prompt = f"""Correct only the incomplete engineering requirement contracts below.

Document: {doc_name} {chunk_label}
Source text:
{source_text}

Previous contracts:
{prior}

Return one corrected requirement for every previous req_code. Preserve the exact regulatory identifier.
Decompose every independently pass/fail predicate: trigger or applicability, required action, required
outcome, referenced equipment/conformance, numeric limit, measurement, calculation, and reporting step.
Mark triggers and contextual gates APPLICABILITY; mark auditable obligations VERIFICATION. For IF/THEN
text, create one APPLICABILITY antecedent and every VERIFICATION consequent. For alternatives use ANY_OF
and list only alternative VERIFICATION paths in condition_ids. For joined ALL_OF clauses do not collapse
several predicates into a summary condition.
Build exhaustive clause_coverage and set contract_complete=true only if nothing is omitted. Return JSON only.
"""
    try:
        corrected = await generate_structured(
            prompt=prompt,
            response_model=ExtractionResult,
            model=active_model,
            system_instruction=(
                "You are performing a focused contract-completeness audit. Preserve source meaning and IDs; "
                "split independent predicates without inventing obligations."
            ),
            thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
            max_output_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
        )
    except Exception as exc:
        import logging
        logging.getLogger("traceaudit.extraction").warning(
            "Contract completeness retry failed for %s: %s", chunk_label, exc
        )
        return requirements
    if not corrected or not corrected.requirements:
        return requirements

    normalized = _normalize_extracted_requirements(corrected.requirements)
    corrected_by_code = {item.req_code: item for item in normalized}
    output: list[ExtractedRequirement] = []
    for original in requirements:
        candidate = corrected_by_code.get(original.req_code)
        if candidate and (
            len(candidate.conditions) > len(original.conditions)
            or (candidate.contract_complete and not original.contract_complete)
        ):
            output.append(candidate)
        else:
            output.append(original)
    return output


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
            return await _retry_incomplete_contracts(
                chunk_text,
                normalized,
                doc_name=doc_name,
                active_model=active_model,
                thinking_level=thinking_level,
                chunk_label=chunk_label,
            )

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
        combined = _deduplicate_requirements(normalized + recovered)
        return await _retry_incomplete_contracts(
            chunk_text,
            combined,
            doc_name=doc_name,
            active_model=active_model,
            thinking_level=thinking_level,
            chunk_label=chunk_label,
        )

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
    for chunk_idx, requirements in sorted(chunk_results, key=lambda item: item[0]):
        before = len(all_requirements)
        # Overlapping sections may return the same clause twice. Keep the
        # structurally richer contract instead of whichever response arrived
        # first.
        all_requirements = _deduplicate_requirements(all_requirements + requirements)
        added_count = len(all_requirements) - before
        print(f"    [Extraction {chunk_idx + 1:02d}/{len(text_chunks):02d}] Extracted {added_count} new requirements (Total unique: {len(all_requirements)})", flush=True)

    if all_requirements:
        return all_requirements

    return fallback_extract_requirements(text, doc_name)
