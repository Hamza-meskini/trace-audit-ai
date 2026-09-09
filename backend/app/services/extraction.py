"""Requirement extraction service.

Uses the configured LLM provider (Databricks, TokenRouter/GLM, Gemini, or OpenAI)
with structured schema validation, falling back to a deterministic rule-based
parser when no API key is provided or when running offline.
"""

import re
import asyncio
from decimal import Decimal, InvalidOperation
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
    source_span: Optional[str] = Field(
        None,
        description="Shortest verbatim source span that grounds this condition",
    )
    source_parameter: Optional[str] = Field(
        None,
        description="Subject/property wording used by the source document",
    )
    canonical_parameter: Optional[str] = Field(
        None,
        description="Stable snake_case semantic name used by retrieval and verification",
    )
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
    clause_ids: list[str] = Field(
        default_factory=list,
        description="Semantic clause IDs formalized by this condition",
    )

    @field_validator("clause_ids", mode="before")
    @classmethod
    def coerce_null_clause_ids(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value

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

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        raw = str(value).strip().lower().replace(" ", "_")
        aliases = {
            "at_least": ">=", "gte": ">=", "greater_than_or_equal": ">=", "greater_than_or_equal_to": ">=",
            "at_most": "<=", "lte": "<=", "less_than_or_equal": "<=", "less_than_or_equal_to": "<=",
            "not_exceeding": "<=", "greater_than": ">", "less_than": "<", "below": "<", "later_than": ">",
            "equal": "==", "equals": "==", "equal_to": "==", "=": "==",
            "within": "between", "in_range": "between",
        }
        return aliases.get(raw, raw or None)


class ExtractedClauseCoverage(BaseModel):
    """Mapping from an obligation-bearing source clause to atomic conditions."""

    clause_id: Optional[str] = Field(None, description="Semantic clause identifier, e.g. CL1")
    clause: str = Field(description="Exact or minimally normalized obligation-bearing clause")
    condition_ids: list[str] = Field(
        default_factory=list,
        description="IDs of all atomic conditions that formalize this clause",
    )


class ExtractedSemanticClause(BaseModel):
    """One grounded proposition identified before atomic condition construction."""

    clause_id: str = Field(description="Sequential identifier CL1, CL2, ...")
    clause_type: str = Field(
        "VERIFICATION",
        pattern="^(APPLICABILITY|VERIFICATION|QUALIFIER)$",
        description="Whether this is a trigger, auditable obligation, or non-atomic qualifier",
    )
    source_span: str = Field(description="Shortest verbatim text span expressing the proposition")
    subject: Optional[str] = None
    predicate: Optional[str] = None
    relationship: Optional[str] = Field(
        None,
        description="Local relationship such as AND, OR, IF, THEN, BEFORE, AFTER, or NONE",
    )


class DiscoveredRequirement(BaseModel):
    """Requirement identity and source text, deliberately excluding atomic contracts."""

    req_code: str
    title: str
    description: str
    category: str = "General"
    severity: str = "Medium"


class RequirementDiscoveryResult(BaseModel):
    requirements: list[DiscoveredRequirement] = Field(default_factory=list)

    @field_validator("requirements", mode="before")
    @classmethod
    def coerce_null_requirements(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value


class RequirementClausePlan(BaseModel):
    """Grounded semantic plan produced before the atomic JSON contract."""

    req_code: str
    semantic_clauses: list[ExtractedSemanticClause] = Field(default_factory=list)
    logic_tree: Optional[dict[str, Any]] = None
    decomposition_confidence: float = Field(0.0, ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)

    @field_validator("semantic_clauses", "ambiguities", mode="before")
    @classmethod
    def coerce_null_collections(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value


class RequirementPlanningResult(BaseModel):
    plans: list[RequirementClausePlan] = Field(default_factory=list)

    @field_validator("plans", mode="before")
    @classmethod
    def coerce_null_plans(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value


class ExtractedRequirementLogic(BaseModel):
    """Boolean relationship between the extracted atomic conditions."""

    operator: str = Field("ALL_OF", pattern="^(ALL_OF|ANY_OF|IF_THEN)$")
    condition_ids: list[str] = Field(default_factory=list)
    if_condition_id: Optional[str] = None
    then_condition_ids: list[str] = Field(default_factory=list)

    @field_validator("condition_ids", "then_condition_ids", mode="before")
    @classmethod
    def coerce_null_lists(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value



class ExtractedRequirement(BaseModel):
    req_code: str = Field(description="Requirement identifier, e.g. REQ-001")
    title: str = Field(description="Short concise summary of requirement")
    description: Optional[str] = Field(None, description="Full requirement text")
    category: str = Field("General", description="Category: Electrical, Safety, Environmental, Mechanical, Cybersecurity, Documentation")
    severity: str = Field("Medium", description="Severity: Critical, High, Medium, Low")
    parameters: list[ExtractedParameter] = Field(default_factory=list)
    conditions: list[ExtractedCondition] = Field(default_factory=list)
    logic: ExtractedRequirementLogic = Field(default_factory=ExtractedRequirementLogic)
    logic_tree: Optional[dict[str, Any]] = Field(
        None,
        description="Nested boolean expression; flat logic remains available for verifier compatibility",
    )
    semantic_clauses: list[ExtractedSemanticClause] = Field(default_factory=list)
    clause_coverage: list[ExtractedClauseCoverage] = Field(default_factory=list)
    unmapped_obligations: list[str] = Field(default_factory=list)
    decomposition_confidence: float = Field(0.0, ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    decomposition_method: str = Field(
        "legacy",
        pattern="^(legacy|staged|deterministic_fallback)$",
    )
    contract_complete: bool = Field(
        False,
        description="True only when every obligation-bearing clause maps to one or more conditions",
    )

    @field_validator("logic", mode="before")
    @classmethod
    def coerce_logic_string(cls, value: Any) -> Any:
        """Coerce harmless model variations without rejecting the requirement.

        A missing/null logic object means the model did not specify a special
        gate.  Treat it as the schema default (ALL_OF) so validation and the
        focused completeness repair can inspect the contract instead of
        discarding the complete requirement payload.
        """
        if value is None:
            return {}
        if isinstance(value, str):
            return {"operator": value}
        return value

    @field_validator(
        "parameters", "conditions", "semantic_clauses", "clause_coverage",
        "unmapped_obligations", "ambiguities", "validation_issues", mode="before",
    )
    @classmethod
    def coerce_null_collections(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value


class ExtractionResult(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)

    @field_validator("requirements", mode="before")
    @classmethod
    def coerce_null_requirements(cls, value: Any) -> list[Any] | Any:
        return [] if value is None else value


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
EXTRACTION_REPAIR_ATTEMPTS = 1
EXTRACTION_SCHEMA_RETRY_ATTEMPTS = 1

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


_OBLIGATION_TEXT_RE = re.compile(
    r"\b(?:shall|must|required\s+to|is\s+required|are\s+required)\b",
    re.IGNORECASE,
)


def _source_requirement_description(block: str, fallback: str) -> str:
    """Recover a complete normative sentence from an identified source block.

    This is source grounding, not semantic decomposition.  It is used only
    when discovery returned a heading or otherwise dropped the obligation.
    Wrapped PDF lines are joined until the normative sentence terminates.
    """
    lines = [" ".join(line.split()) for line in (block or "").splitlines() if line.strip()]
    start = next((index for index, line in enumerate(lines) if _OBLIGATION_TEXT_RE.search(line)), None)
    if start is None:
        return fallback

    recovered: list[str] = []
    for line in lines[start:]:
        # A page footer or a following structured section must not become part
        # of the requirement. Normally the terminating punctuation stops first.
        if recovered and re.match(r"^(?:SECTION:|Page\s+\d+\s+of\s+\d+\b)", line, re.IGNORECASE):
            break
        recovered.append(line)
        if re.search(r"[.!?]\s*$", line):
            break
    return " ".join(recovered).strip() or fallback


def _ground_discovered_description(
    requirement: DiscoveredRequirement,
    source_block: str,
) -> DiscoveredRequirement:
    """Restore source prose when discovery returned only an ID/title."""
    description = requirement.description or ""
    if _OBLIGATION_TEXT_RE.search(description):
        return requirement
    requirement.description = _source_requirement_description(source_block, description)
    return requirement


def _normalize_requirement_code(value: str) -> str:
    code = (value or "").strip().upper().replace("_", "-")
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

    def quality(item: ExtractedRequirement) -> tuple[int, int, float, int, int]:
        return (
            -len(item.validation_issues),
            int(item.contract_complete),
            item.decomposition_confidence,
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


_GENERIC_PARAMETER_NAMES = {
    "condition", "duration", "feature", "limit", "measurement", "parameter",
    "property", "range", "requirement", "result", "state", "status", "threshold",
    "time", "value",
}


def _normalized_source_text(value: str) -> str:
    """Normalize typography while retaining comparison semantics for grounding."""
    text = (value or "").lower()
    replacements = {
        "≤": " <= ", "≥": " >= ", "°": " deg ", "µ": "u", "μ": "u",
        "–": "-", "—": "-", "“": '"', "”": '"', "’": "'",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return " ".join(re.findall(r"[a-z0-9]+|<=|>=|==|<|>|%", text))


def _source_span_is_grounded(span: Optional[str], source: str) -> bool:
    """Accept literal/minimally normalized spans, not free-form paraphrases."""
    normalized_span = _normalized_source_text(span or "")
    normalized_source = _normalized_source_text(source)
    if not normalized_span or not normalized_source:
        return False
    if normalized_span in normalized_source:
        return True
    span_tokens = normalized_span.split()
    source_tokens = set(normalized_source.split())
    # PDF extraction may move punctuation or table-cell boundaries. Require
    # nearly all substantive tokens so a paraphrase cannot masquerade as a quote.
    if len(span_tokens) < 4:
        return all(token in source_tokens for token in span_tokens)
    return sum(token in source_tokens for token in span_tokens) / len(span_tokens) >= 0.9


def _logic_leaf_ids(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return []
    output: list[str] = []
    condition_id = node.get("condition_id")
    if condition_id:
        output.append(str(condition_id))
    for child in node.get("children") or []:
        output.extend(_logic_leaf_ids(child))
    for key in ("antecedent", "consequent", "if", "then"):
        output.extend(_logic_leaf_ids(node.get(key)))
    return list(dict.fromkeys(output))


def _logic_tree_structure_issues(node: Any, known_ids: set[str], path: str = "logic_tree") -> list[str]:
    if not isinstance(node, dict):
        return [f"{path} is not an object."]
    operator = str(node.get("operator") or "").upper()
    if operator == "CONDITION":
        condition_id = str(node.get("condition_id") or "")
        if not condition_id:
            return [f"{path} CONDITION leaf has no condition_id."]
        if condition_id not in known_ids:
            return [f"{path} references unknown condition {condition_id}."]
        return []
    if operator in {"ALL_OF", "ANY_OF"}:
        children = node.get("children") or []
        issues = [] if children else [f"{path} {operator} node has no children."]
        if operator == "ANY_OF" and len(children) < 2:
            issues.append(f"{path} ANY_OF node must contain at least two alternatives.")
        for index, child in enumerate(children, 1):
            issues.extend(_logic_tree_structure_issues(child, known_ids, f"{path}.children[{index}]"))
        return issues
    if operator == "IF_THEN":
        antecedent = node.get("antecedent") or node.get("if")
        consequent = node.get("consequent") or node.get("then")
        issues: list[str] = []
        if antecedent is None:
            issues.append(f"{path} IF_THEN node has no antecedent.")
        else:
            issues.extend(_logic_tree_structure_issues(antecedent, known_ids, f"{path}.antecedent"))
        if consequent is None:
            issues.append(f"{path} IF_THEN node has no consequent.")
        else:
            issues.extend(_logic_tree_structure_issues(consequent, known_ids, f"{path}.consequent"))
        return issues
    return [f"{path} uses unsupported operator '{operator or 'missing'}'."]


def _logic_tree_from_flat(logic: ExtractedRequirementLogic) -> dict[str, Any]:
    def leaf(condition_id: str) -> dict[str, str]:
        return {"operator": "CONDITION", "condition_id": condition_id}

    if logic.operator == "IF_THEN":
        then_nodes = [leaf(item) for item in logic.then_condition_ids]
        consequent: dict[str, Any]
        if len(then_nodes) == 1:
            consequent = then_nodes[0]
        else:
            consequent = {"operator": "ALL_OF", "children": then_nodes}
        return {
            "operator": "IF_THEN",
            "antecedent": leaf(logic.if_condition_id) if logic.if_condition_id else None,
            "consequent": consequent,
        }
    return {
        "operator": logic.operator,
        "children": [leaf(item) for item in logic.condition_ids],
    }


_LOGIC_OPERATORS = {"CONDITION", "ALL_OF", "ANY_OF", "IF_THEN"}


def _canonicalize_logic_tree(
    tree: Any,
    flat_logic: ExtractedRequirementLogic,
    known_ids: set[str],
) -> dict[str, Any]:
    """Canonicalize equivalent model-produced logic syntax without changing semantics.

    Models commonly serialize the same graph as ``condition_ids``, keyed
    operators (``{"ALL_OF": ...}``), or string leaves. Downstream validation
    accepts one representation, so normalize those syntactic variants before
    deciding that an LLM repair is necessary.
    """
    id_lookup = {item.upper(): item for item in known_ids}

    def condition_id(value: Any) -> str:
        raw = str(value or "").strip()
        return id_lookup.get(raw.upper(), raw)

    def leaf(value: Any) -> Optional[dict[str, str]]:
        identifier = condition_id(value)
        return {"operator": "CONDITION", "condition_id": identifier} if identifier else None

    def branch(value: Any, default_operator: str = "ALL_OF") -> Optional[dict[str, Any]]:
        if isinstance(value, list):
            children = [item for child in value if (item := node(child)) is not None]
            if not children:
                return None
            if len(children) == 1:
                return children[0]
            return {"operator": default_operator, "children": children}
        return node(value)

    def node(value: Any) -> Optional[dict[str, Any]]:
        if isinstance(value, str):
            return leaf(value)
        if isinstance(value, list):
            return branch(value)
        if not isinstance(value, dict):
            return None

        operator = str(value.get("operator") or value.get("type") or "").upper()
        payload: Any = value

        # Accept keyed nodes such as {"ALL_OF": {"children": [...]}} and
        # {"IF_THEN": {"antecedent": "C1", "consequent": "C2"}}.
        if not operator:
            for key, candidate in value.items():
                keyed_operator = str(key).upper()
                if keyed_operator in _LOGIC_OPERATORS:
                    operator = keyed_operator
                    payload = candidate
                    break

        # Some providers emit {"CONDITION":"ALL_OF","children":[...]},
        # using CONDITION as a discriminator key rather than a leaf operator.
        condition_discriminator = str(value.get("CONDITION") or "").upper()
        if operator == "CONDITION" and condition_discriminator in _LOGIC_OPERATORS - {"CONDITION"}:
            operator = condition_discriminator
            payload = value

        if not operator and value.get("condition_id"):
            return leaf(value.get("condition_id"))
        if not operator and value.get("children"):
            operator = "ALL_OF"

        if operator == "CONDITION":
            identifier: Any = value.get("condition_id") or value.get("id")
            if identifier is None:
                if isinstance(payload, dict):
                    identifier = payload.get("condition_id") or payload.get("id")
                elif str(payload or "").upper() not in _LOGIC_OPERATORS:
                    identifier = payload
            return leaf(identifier)

        source = payload if isinstance(payload, dict) else value
        if operator in {"ALL_OF", "ANY_OF"}:
            children_source: Any = payload if isinstance(payload, list) else (
                source.get("children")
                or source.get("condition_ids")
                or source.get("conditions")
                or []
            )
            if not isinstance(children_source, list):
                children_source = [children_source]
            children = [item for child in children_source if (item := node(child)) is not None]
            return {"operator": operator, "children": children}

        if operator == "IF_THEN":
            antecedent_source = (
                source.get("antecedent")
                or source.get("if")
                or source.get("if_condition_id")
            )
            consequent_source = (
                source.get("consequent")
                or source.get("then")
                or source.get("then_condition_ids")
            )
            return {
                "operator": "IF_THEN",
                "antecedent": branch(antecedent_source),
                "consequent": branch(consequent_source),
            }
        return None

    return node(tree) or _logic_tree_from_flat(flat_logic)


def _canonical_numeric_token(value: Any) -> Optional[str]:
    """Normalize cosmetic numeric spelling such as +70/70 and 2.0/2."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return "0" if normalized in {"-0", "+0", ""} else normalized.lstrip("+")


def _numeric_values(condition: ExtractedCondition) -> set[str]:
    values: set[str] = set()
    for value in (condition.threshold, condition.min_value, condition.max_value):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            normalized = _canonical_numeric_token(value)
            if normalized is not None:
                values.add(normalized)
        elif isinstance(value, str):
            values.update(
                normalized
                for token in re.findall(r"[+-]?\d+(?:\.\d+)?", value)
                if (normalized := _canonical_numeric_token(token)) is not None
            )
    return values


def _contract_validation_issues(
    requirement: ExtractedRequirement,
    source_text: str,
) -> list[str]:
    """Return structural/provenance issues without rewriting model semantics."""
    issues: list[str] = []
    conditions = requirement.conditions
    known_ids = {item.condition_id for item in conditions if item.condition_id}
    clause_by_id = {
        item.clause_id: item for item in requirement.semantic_clauses if item.clause_id
    }
    if not conditions:
        issues.append("No atomic conditions were produced.")
    if not requirement.semantic_clauses:
        issues.append("No semantic clause plan was produced.")

    seen_clause_ids: set[str] = set()
    for clause in requirement.semantic_clauses:
        if clause.clause_id in seen_clause_ids:
            issues.append(f"Duplicate semantic clause ID {clause.clause_id}.")
        seen_clause_ids.add(clause.clause_id)
        if not _source_span_is_grounded(clause.source_span, source_text):
            issues.append(f"Semantic clause {clause.clause_id} is not grounded in the source text.")

    covered_clause_ids: set[str] = set()
    covered_condition_ids: set[str] = set()
    for mapping in requirement.clause_coverage:
        if mapping.clause_id:
            if mapping.clause_id not in clause_by_id:
                issues.append(f"Coverage references unknown semantic clause {mapping.clause_id}.")
            else:
                covered_clause_ids.add(mapping.clause_id)
        elif not _source_span_is_grounded(mapping.clause, source_text):
            issues.append("A clause-coverage entry has neither a known clause_id nor a grounded clause.")
        for condition_id in mapping.condition_ids:
            if condition_id not in known_ids:
                issues.append(f"Coverage references unknown condition {condition_id}.")
            else:
                covered_condition_ids.add(condition_id)

    normative_clauses = {
        item.clause_id for item in requirement.semantic_clauses
        if item.clause_type in {"APPLICABILITY", "VERIFICATION"}
    }
    for clause_id in sorted(normative_clauses - covered_clause_ids):
        issues.append(f"Normative semantic clause {clause_id} is not mapped to a condition.")

    predicate_keys: set[tuple[Any, ...]] = set()
    for condition in conditions:
        if not condition.source_parameter:
            issues.append(f"Condition {condition.condition_id} has no source_parameter.")
        canonical = (condition.canonical_parameter or condition.parameter or "").strip().lower()
        if not canonical:
            issues.append(f"Condition {condition.condition_id} has no canonical_parameter.")
        elif canonical in _GENERIC_PARAMETER_NAMES:
            issues.append(f"Condition {condition.condition_id} uses generic parameter '{canonical}'.")
        if not _source_span_is_grounded(condition.source_span, source_text):
            issues.append(f"Condition {condition.condition_id} has no grounded source_span.")
        if not condition.clause_ids:
            issues.append(f"Condition {condition.condition_id} is not linked to a semantic clause.")
        for clause_id in condition.clause_ids:
            if clause_id not in clause_by_id:
                issues.append(f"Condition {condition.condition_id} references unknown clause {clause_id}.")

        key = (
            condition.condition_role,
            canonical,
            condition.operator,
            str(condition.threshold),
            condition.min_value,
            condition.max_value,
            (condition.unit or "").lower(),
        )
        if key in predicate_keys:
            issues.append(f"Condition {condition.condition_id} duplicates another atomic predicate.")
        predicate_keys.add(key)

    for condition_id in sorted(known_ids - covered_condition_ids):
        issues.append(f"Condition {condition_id} is absent from clause_coverage.")

    for clause in requirement.semantic_clauses:
        if clause.clause_type == "QUALIFIER":
            continue
        source_numbers = {
            normalized
            for token in re.findall(r"[+-]?\d+(?:\.\d+)?", clause.source_span or "")
            if (normalized := _canonical_numeric_token(token)) is not None
        }
        if not source_numbers:
            continue
        mapped_ids = {
            condition_id
            for mapping in requirement.clause_coverage
            if mapping.clause_id == clause.clause_id
            for condition_id in mapping.condition_ids
        }
        mapped_values = set().union(*(
            _numeric_values(condition)
            for condition in conditions
            if condition.condition_id in mapped_ids
        )) if mapped_ids else set()
        # Requirement IDs and table row labels can contain numbers that are not
        # thresholds. Only enforce numeric fidelity when at least one mapped
        # condition is numeric.
        if mapped_values and not source_numbers.issubset(mapped_values):
            missing = ", ".join(sorted(source_numbers - mapped_values))
            issues.append(f"Clause {clause.clause_id} has unmapped numeric value(s): {missing}.")

    tree_ids = set(_logic_leaf_ids(requirement.logic_tree))
    if requirement.logic_tree:
        issues.extend(_logic_tree_structure_issues(requirement.logic_tree, known_ids))
    dangling_tree_ids = tree_ids - known_ids
    if dangling_tree_ids:
        issues.append("logic_tree references unknown condition(s): " + ", ".join(sorted(dangling_tree_ids)) + ".")
    verification_ids = {
        item.condition_id for item in conditions
        if item.condition_role == "VERIFICATION" and item.mandatory and item.condition_id
    }
    if requirement.logic_tree and not verification_ids.issubset(tree_ids):
        issues.append("logic_tree omits mandatory verification condition(s): " + ", ".join(sorted(verification_ids - tree_ids)) + ".")
    tree_operator = str((requirement.logic_tree or {}).get("operator") or "").upper()
    if tree_operator in {"ALL_OF", "ANY_OF", "IF_THEN"} and tree_operator != requirement.logic.operator:
        issues.append(
            f"logic_tree operator {tree_operator} disagrees with legacy logic operator {requirement.logic.operator}."
        )
    if requirement.logic.operator == "IF_THEN" and requirement.logic_tree:
        antecedent_ids = set(_logic_leaf_ids(
            requirement.logic_tree.get("antecedent") or requirement.logic_tree.get("if")
        ))
        consequent_ids = set(_logic_leaf_ids(
            requirement.logic_tree.get("consequent") or requirement.logic_tree.get("then")
        ))
        if requirement.logic.if_condition_id not in antecedent_ids:
            issues.append("Legacy IF_THEN antecedent disagrees with logic_tree.")
        if not set(requirement.logic.then_condition_ids).issubset(consequent_ids):
            issues.append("Legacy IF_THEN consequents disagree with logic_tree.")

    return list(dict.fromkeys(issues))


def _validation_issue_categories(issues: list[str]) -> dict[str, int]:
    """Collapse detailed validation messages into stable diagnostic buckets."""
    categories: dict[str, int] = {}
    for issue in issues:
        normalized = issue.lower()
        if "source_span" in normalized or "not grounded" in normalized:
            category = "source_grounding"
        elif "no atomic conditions" in normalized or "no semantic clause plan" in normalized:
            category = "missing_structure"
        elif "parameter" in normalized:
            category = "parameter_schema"
        elif "numeric value" in normalized:
            category = "numeric_fidelity"
        elif "logic_tree" in normalized or "if_then" in normalized:
            category = "logic_tree"
        elif "duplicate" in normalized:
            category = "duplicate_atom"
        elif "clause" in normalized or "coverage" in normalized:
            category = "clause_mapping"
        else:
            category = "other"
        categories[category] = categories.get(category, 0) + 1
    return categories


def _format_validation_issue_categories(issues: list[str]) -> str:
    categories = _validation_issue_categories(issues)
    return ", ".join(f"{name}={count}" for name, count in sorted(categories.items())) or "none"


def _semantic_repair_issues(issues: list[str]) -> list[str]:
    """Select only defects for which another semantic LLM pass can add value."""
    repairable_categories = {"missing_structure", "clause_mapping", "logic_tree"}
    return [
        issue for issue in issues
        if any(
            category in repairable_categories
            for category in _validation_issue_categories([issue])
        )
    ]


def _normalize_extracted_requirements(
    requirements: list[ExtractedRequirement],
    *,
    source_by_code: Optional[dict[str, str]] = None,
    validate_staged: bool = True,
) -> list[ExtractedRequirement]:
    """Normalize stable IDs while preserving the model's semantic contract."""
    for req in requirements:
        req.req_code = _normalize_requirement_code(req.req_code)
        for index, condition in enumerate(req.conditions, 1):
            if not condition.condition_id:
                condition.condition_id = f"{req.req_code}-C{index}"
            if not condition.description.strip():
                condition.description = condition.parameter or req.title
            if req.decomposition_method != "staged":
                if not condition.source_parameter:
                    condition.source_parameter = condition.parameter
                if not condition.canonical_parameter:
                    condition.canonical_parameter = condition.parameter or condition.source_parameter
            if not condition.parameter:
                condition.parameter = condition.canonical_parameter or condition.source_parameter
            condition.clause_ids = list(dict.fromkeys(
                item.strip() for item in condition.clause_ids if item and item.strip()
            ))
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
            if mapping.clause_id:
                mapping.clause_id = mapping.clause_id.strip()
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
        req.logic_tree = _canonicalize_logic_tree(req.logic_tree, req.logic, condition_ids)
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
        if validate_staged and req.decomposition_method == "staged":
            source = (source_by_code or {}).get(req.req_code, req.description or req.title)
            req.validation_issues = _contract_validation_issues(req, source)
            if req.validation_issues:
                req.contract_complete = False
    return requirements


ATOMIC_DECOMPOSITION_GUIDE = """Canonical atomic-decomposition policy:
- One atomic condition represents one independently pass/fail predicate: one subject, one property or action,
  and one expected state or comparison. Assign sequential IDs C1, C2, C3, ... within each requirement.
- Split when the subject, testable property, required action/outcome, numeric dimension, unit, condition role,
  alternative path, or ordering relationship changes.
- Do not split a continuous numeric range into endpoint conditions. Encode it once with operator `between`,
  min_value, max_value, and unit. Do not merge two different quantities merely because they occur in one phrase.
- Never create an umbrella condition that repeats the full source sentence when its independently testable
  predicates are already represented by narrower conditions. A contract may contain either a genuinely atomic
  combined predicate or its independent atoms, but never both as duplicate pass/fail obligations.
- When a performance limit must be verified across an environmental or operating envelope, separate the
  performance limit from independently evidentiable coverage boundaries. Do not also add a combined summary
  condition that duplicates both the limit and its coverage atoms.
- A measurable trigger, precondition, operating point, or test context is an APPLICABILITY condition. Introductory
  prose with no independently testable predicate is not a condition. Conditional text uses IF_THEN.
- When an action/state and its timing can fail independently, represent both: the required action/state and the
  latency or duration. If separately identifiable components must each comply, create one condition per component.
- Preserve ordering as an explicit VERIFICATION predicate; do not reduce a before/after rule to unrelated states.
- Normalize negation into one directly testable property, without adding a duplicate logical inverse.
- Use a specific snake_case parameter formed from the source subject and property (for example,
  `valve_open_latency`), not generic names such as `time`, `value`, `state`, or `result`.
- Do not create an atom for a heading, explanation, figure caption, or test method unless it imposes a normative,
  independently verifiable constraint. Never invent a condition just to fit an example.

Domain-neutral examples:

Example 1 — continuous range (do not split endpoints)
Source: "The sensor shall operate between -20 deg C and 80 deg C."
Conditions:
  C1 VERIFICATION: parameter=operating_temperature, operator="between",
      min_value=-20, max_value=80, unit=deg C
Logic: ALL_OF [C1]

Example 2 — trigger, required state, and independent latency
Source: "If line pressure exceeds 4 bar, the relief valve shall open within 200 ms."
Conditions:
  C1 APPLICABILITY: parameter=line_pressure, operator=">", threshold=4, unit=bar
  C2 VERIFICATION: parameter=relief_valve_open, operator="==", threshold=true
  C3 VERIFICATION: parameter=relief_valve_open_latency, operator="<=", threshold=200, unit=ms
Logic: IF_THEN, if_condition_id=C1, then_condition_ids=[C2, C3]

Example 3 — independently testable components
Source: "The primary and backup indicators shall each illuminate green."
Conditions:
  C1 VERIFICATION: parameter=primary_indicator_color, operator="==", threshold=green
  C2 VERIFICATION: parameter=backup_indicator_color, operator="==", threshold=green
Logic: ALL_OF [C1, C2]

Example 4 — explicit ordering
Source: "The device shall authenticate the update package before installation begins."
Conditions:
  C1 VERIFICATION: parameter=update_package_authenticated, operator="==", threshold=true
  C2 VERIFICATION: parameter=installation_after_authentication, operator="==", threshold=true
Logic: ALL_OF [C1, C2]

Example 5 — negation plus duration
Source: "No telemetry frame shall be lost during a 10-minute observation."
Conditions:
  C1 APPLICABILITY: parameter=observation_duration, operator=">=", threshold=10, unit=min
  C2 VERIFICATION: parameter=telemetry_frame_loss_count, operator="==", threshold=0, unit=count
Logic: IF_THEN, if_condition_id=C1, then_condition_ids=[C2]

Example 6 — true alternatives
Source: "The alarm shall be indicated by either a red lamp or an audible tone."
Conditions:
  C1 VERIFICATION: parameter=red_alarm_lamp_active, operator="==", threshold=true
  C2 VERIFICATION: parameter=audible_alarm_tone_active, operator="==", threshold=true
Logic: ANY_OF [C1, C2]

Example 7 — performance across a verification envelope (no umbrella duplicate)
Source: "Measurement error shall not exceed 2 units from -10 deg C through 50 deg C."
Conditions:
  C1 VERIFICATION: parameter=measurement_absolute_error, operator="<=", threshold=2, unit=unit
  C2 APPLICABILITY: parameter=verified_temperature_lower_bound, operator="<=", threshold=-10, unit=deg C
  C3 APPLICABILITY: parameter=verified_temperature_upper_bound, operator=">=", threshold=50, unit=deg C
Logic: ALL_OF [C1, C2, C3]
"""


def _build_discovery_prompt(chunk_text: str, doc_name: str, chunk_label: str) -> str:
    return f"""Discover engineering requirements in this document excerpt. This is identity discovery only.
Do not create atomic conditions, parameters, logic, or clause mappings in this stage.

Document: {doc_name} {chunk_label}
Source:
{chunk_text}

Return each normative requirement with:
- req_code: preserve the exact existing identifier; never invent a replacement when one is visible
- title: a concise source-faithful summary
- description: the complete requirement source text, including joined clauses and relevant recovered table/figure text
- category and severity

Exclude headings, explanatory notes, and test results that do not impose an obligation. Return JSON only.
"""


def _build_clause_planning_prompt(
    requirements: list[DiscoveredRequirement],
    source_text: str,
    doc_name: str,
    chunk_label: str,
) -> str:
    discovered = [item.model_dump() for item in requirements]
    return f"""Create a semantic clause plan for every discovered requirement before any atomic JSON is constructed.

Document: {doc_name} {chunk_label}
Full source excerpt:
{source_text}

Discovered requirements:
{discovered}

For every req_code:
1. Enumerate the smallest source-grounded propositions as CL1, CL2, ... in reading order.
2. Classify each as APPLICABILITY (trigger/precondition), VERIFICATION (auditable obligation), or
   QUALIFIER (important context that is not independently pass/fail).
3. Copy the shortest verbatim source_span for every clause. Do not paraphrase source_span.
4. Record subject, predicate, and local relationship (AND, OR, IF, THEN, BEFORE, AFTER, or NONE).
5. Build a nested logic_tree over clause IDs using CONDITION, ALL_OF, ANY_OF, and IF_THEN nodes.
   CONDITION leaves use `clause_id`; IF_THEN uses `antecedent` and `consequent`; ALL_OF/ANY_OF use `children`.
6. Report genuine ambiguities and a calibrated decomposition_confidence from 0 to 1.

QUALIFIER is valid only in this semantic plan. It is context for interpreting nearby obligations and must not
later become an atomic condition. Do not produce atomic conditions in this planning stage. Do not add a
proposition merely because a noun, number, table cell, or figure label is present. Return one plan for every
supplied req_code. Return JSON only.
"""


def _build_atomic_contract_prompt(
    requirement: DiscoveredRequirement,
    plan: RequirementClausePlan,
    source_text: str,
    doc_name: str,
) -> str:
    return f"""Convert one approved semantic clause plan into an auditable atomic requirement contract.

Document: {doc_name}
Requirement identity:
{requirement.model_dump()}

Authoritative source block:
{source_text}

Semantic clause plan:
{plan.model_dump()}

Return exactly one requirement using req_code {requirement.req_code}. Preserve its source description.
For every atomic condition:
- use a sequential condition_id C1, C2, ...
- map one independently pass/fail predicate only
- condition_role must be exactly APPLICABILITY or VERIFICATION; never use QUALIFIER for a condition
- copy the shortest verbatim `source_span` that proves the condition came from the source
- set `source_parameter` to the source's subject/property wording
- set `canonical_parameter` to a precise snake_case semantic name
- also set legacy `parameter` equal to canonical_parameter for verifier compatibility
- list every semantic `clause_ids` formalized by the condition
- provide operator/value/unit whenever expressed by the source

QUALIFIER clauses remain only in semantic_clauses: do not convert them to C conditions, do not include them
in logic, and do not require them in clause_coverage. Build `clause_coverage` with both clause_id and source
clause text. Every APPLICABILITY and VERIFICATION semantic clause must map to at least one condition, and
every condition must be covered. Build a nested
condition `logic_tree` using CONDITION leaves with condition_id, ALL_OF/ANY_OF children, and IF_THEN
antecedent/consequent. Also populate the legacy flat `logic` as the closest top-level representation.
Carry forward real ambiguities, set a calibrated decomposition_confidence, set decomposition_method to
`staged`, and set contract_complete true only when the plan is exhaustively and consistently represented.

{ATOMIC_DECOMPOSITION_GUIDE}
"""


def _discovered_source_block(
    requirement: DiscoveredRequirement,
    source_by_code: dict[str, str],
    fallback_source: str,
) -> str:
    # For unnumbered requirements, relevant table/figure text may surround the
    # sentence selected during discovery. Preserve that local chunk so the
    # planner and provenance validator can still see the multimodal context.
    return source_by_code.get(requirement.req_code, fallback_source or requirement.description)


def _fallback_clause_plan(requirement: DiscoveredRequirement) -> RequirementClausePlan:
    return RequirementClausePlan(
        req_code=requirement.req_code,
        semantic_clauses=[ExtractedSemanticClause(
            clause_id="CL1",
            clause_type="VERIFICATION",
            source_span=requirement.description,
            subject=requirement.title,
            predicate=requirement.description,
            relationship="NONE",
        )],
        logic_tree={"operator": "CONDITION", "clause_id": "CL1"},
        decomposition_confidence=0.0,
        ambiguities=["The semantic clause planner was unavailable; human review is required."],
    )


async def _discover_requirements(
    chunk_text: str,
    *,
    doc_name: str,
    active_model: str,
    thinking_level: Optional[str],
    chunk_label: str,
    allow_rule_fallback: bool,
    allow_model_fallback: bool,
) -> list[DiscoveredRequirement]:
    async def request(text: str, label: str) -> list[DiscoveredRequirement]:
        try:
            result = await generate_structured(
                prompt=_build_discovery_prompt(text, doc_name, label),
                response_model=RequirementDiscoveryResult,
                model=active_model,
                system_instruction=(
                    "You discover normative engineering requirements without attempting atomic decomposition. "
                    "Preserve exact identifiers and complete source clauses. Return complete JSON only."
                ),
                thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
                max_output_tokens=4096,
                allow_model_fallback=allow_model_fallback,
            )
        except Exception as exc:
            import logging
            logging.getLogger("traceaudit.extraction").warning(
                "Requirement discovery failed for %s: %s", label, exc
            )
            return []
        return list(result.requirements) if result else []

    discovered = await request(chunk_text, chunk_label)
    for item in discovered:
        item.req_code = _normalize_requirement_code(item.req_code)
    by_code = {item.req_code: item for item in discovered}
    expected_blocks = {
        code: block
        for block in _requirement_blocks(chunk_text)
        if (code := _requirement_code_from_block(block)) is not None
    }
    missing = {code: block for code, block in expected_blocks.items() if code not in by_code}
    if missing:
        print(
            f"    [Discovery retry] {chunk_label} omitted {len(missing)} identified source block(s).",
            flush=True,
        )
        recovered = await request("\n\n".join(missing.values()), f"{chunk_label}.missing")
        for item in recovered:
            item.req_code = _normalize_requirement_code(item.req_code)
            by_code.setdefault(item.req_code, item)

    still_missing = {code: block for code, block in expected_blocks.items() if code not in by_code}
    if still_missing and not allow_rule_fallback:
        raise RuntimeError(
            "Requirement discovery omitted identified source block(s) after retry: "
            + ", ".join(still_missing)
        )
    if still_missing:
        for code, block in still_missing.items():
            recovered = fallback_extract_requirements(block, doc_name)
            item = next((candidate for candidate in recovered if candidate.req_code == code), None)
            if item:
                by_code[code] = DiscoveredRequirement(
                    req_code=item.req_code,
                    title=item.title,
                    description=item.description or item.title,
                    category=item.category,
                    severity=item.severity,
                )

    if by_code:
        return list(by_code.values())
    if not allow_rule_fallback:
        raise RuntimeError(
            f"The requested extraction model {active_model!r} produced no valid requirement discovery "
            f"for {chunk_label}."
        )
    fallback = fallback_extract_requirements(chunk_text, doc_name)
    return [DiscoveredRequirement(
        req_code=item.req_code,
        title=item.title,
        description=item.description or item.title,
        category=item.category,
        severity=item.severity,
    ) for item in fallback]


async def _plan_requirement_clauses(
    discovered: list[DiscoveredRequirement],
    chunk_text: str,
    *,
    doc_name: str,
    active_model: str,
    fallback_model: Optional[str],
    thinking_level: Optional[str],
    chunk_label: str,
    allow_rule_fallback: bool,
    allow_model_fallback: bool,
) -> dict[str, RequirementClausePlan]:
    async def request(items: list[DiscoveredRequirement], label: str) -> list[RequirementClausePlan]:
        models = list(dict.fromkeys(
            model for model in (active_model, fallback_model) if model
        ))
        for model_index, request_model in enumerate(models):
            try:
                result = await generate_structured(
                    prompt=_build_clause_planning_prompt(items, chunk_text, doc_name, label),
                    response_model=RequirementPlanningResult,
                    model=request_model,
                    system_instruction=(
                        "You build a source-grounded semantic clause graph before atomic condition construction. "
                        "Do not paraphrase source spans or invent obligations. Return complete JSON only."
                    ),
                    thinking_level=thinking_level or settings.ATOMIC_DECOMPOSITION_THINKING_LEVEL,
                    max_output_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
                    # The explicit atomic fallback above is auditable; do not
                    # cascade silently into unrelated provider models here.
                    allow_model_fallback=False,
                )
            except Exception as exc:
                import logging
                logging.getLogger("traceaudit.extraction").warning(
                    "Semantic clause planning failed for %s with %s: %s",
                    label,
                    request_model,
                    exc,
                )
                result = None
            if result:
                return list(result.plans)
            if model_index + 1 < len(models):
                print(
                    f"    [Atomic model fallback] Clause planning for {label}: "
                    f"{request_model} returned no valid result; trying {models[model_index + 1]}.",
                    flush=True,
                )
        return []

    plans = await request(discovered, chunk_label)
    by_code: dict[str, RequirementClausePlan] = {}
    for plan in plans:
        plan.req_code = _normalize_requirement_code(plan.req_code)
        by_code[plan.req_code] = plan
    missing = [item for item in discovered if item.req_code not in by_code]
    if missing:
        repaired = await request(missing, f"{chunk_label}.missing-plans")
        for plan in repaired:
            plan.req_code = _normalize_requirement_code(plan.req_code)
            by_code.setdefault(plan.req_code, plan)

    if missing and not allow_rule_fallback:
        still_missing = [item.req_code for item in discovered if item.req_code not in by_code]
        if still_missing:
            raise RuntimeError(
                "The semantic clause planner omitted requirement(s): " + ", ".join(still_missing)
            )
    for item in discovered:
        by_code.setdefault(item.req_code, _fallback_clause_plan(item))
    return by_code


def _apply_plan_to_contract(
    candidate: ExtractedRequirement,
    discovered: DiscoveredRequirement,
    plan: RequirementClausePlan,
) -> ExtractedRequirement:
    """Attach trusted stage outputs while retaining the model's atomic semantics."""
    candidate.req_code = discovered.req_code
    candidate.title = discovered.title
    candidate.description = discovered.description
    candidate.category = discovered.category
    candidate.severity = discovered.severity
    candidate.semantic_clauses = list(plan.semantic_clauses)
    candidate.ambiguities = list(dict.fromkeys(plan.ambiguities + candidate.ambiguities))
    if candidate.decomposition_confidence <= 0:
        candidate.decomposition_confidence = plan.decomposition_confidence
    else:
        candidate.decomposition_confidence = min(
            candidate.decomposition_confidence,
            plan.decomposition_confidence,
        )
    candidate.decomposition_method = "staged"
    return candidate


async def _construct_atomic_contract(
    discovered: DiscoveredRequirement,
    plan: RequirementClausePlan,
    source_text: str,
    *,
    doc_name: str,
    active_model: str,
    fallback_model: Optional[str],
    thinking_level: Optional[str],
    allow_rule_fallback: bool,
    allow_model_fallback: bool,
) -> ExtractedRequirement:
    async def request(prompt: str, instruction: str) -> Optional[ExtractedRequirement]:
        models = list(dict.fromkeys(
            model for model in (active_model, fallback_model) if model
        ))
        for model_index, request_model in enumerate(models):
            try:
                result = await generate_structured(
                    prompt=prompt,
                    response_model=ExtractionResult,
                    model=request_model,
                    system_instruction=instruction,
                    thinking_level=thinking_level or settings.ATOMIC_DECOMPOSITION_THINKING_LEVEL,
                    max_output_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
                    allow_model_fallback=False,
                )
            except Exception as exc:
                import logging
                logging.getLogger("traceaudit.extraction").warning(
                    "Atomic contract construction failed for %s with %s: %s",
                    discovered.req_code,
                    request_model,
                    exc,
                )
                result = None
            if result and result.requirements:
                matching = next(
                    (
                        item for item in result.requirements
                        if _normalize_requirement_code(item.req_code) == discovered.req_code
                    ),
                    result.requirements[0] if len(result.requirements) == 1 else None,
                )
                if matching:
                    return _apply_plan_to_contract(matching, discovered, plan)
            if model_index + 1 < len(models):
                print(
                    f"    [Atomic model fallback] Contract {discovered.req_code}: "
                    f"{request_model} returned no valid result; trying {models[model_index + 1]}.",
                    flush=True,
                )
        return None

    construction_prompt = _build_atomic_contract_prompt(discovered, plan, source_text, doc_name)
    candidate: Optional[ExtractedRequirement] = None
    for schema_attempt in range(EXTRACTION_SCHEMA_RETRY_ATTEMPTS + 1):
        prompt = construction_prompt
        if schema_attempt:
            prompt += """

CORRECTION RETRY: The previous response did not satisfy the atomic-contract JSON schema. Rebuild the
complete response. In particular, atomic condition_role accepts only APPLICABILITY or VERIFICATION.
QUALIFIER is permitted only inside semantic_clauses and must never appear in conditions or condition logic.
Return exactly one requirement inside the `requirements` array and no explanatory text.
"""
            print(
                f"    [Atomic schema retry] {discovered.req_code}: retrying malformed structured output "
                f"({schema_attempt}/{EXTRACTION_SCHEMA_RETRY_ATTEMPTS}).",
                flush=True,
            )
        candidate = await request(
            prompt,
            (
                "You construct one exhaustive atomic contract from an approved grounded clause plan. "
                "Atomic conditions can only be APPLICABILITY or VERIFICATION; semantic QUALIFIER clauses "
                "must remain non-atomic context. Every condition needs source provenance, precise parameters, "
                "and explicit logic. Return JSON only."
            ),
        )
        if candidate is not None:
            break
    if candidate is None:
        if not allow_rule_fallback:
            raise RuntimeError(f"No valid atomic contract was returned for {discovered.req_code}.")
        fallback = fallback_extract_requirements(source_text, doc_name)
        selected = next((item for item in fallback if item.req_code == discovered.req_code), None)
        if selected is None:
            selected = ExtractedRequirement(
                req_code=discovered.req_code,
                title=discovered.title,
                description=discovered.description,
                category=discovered.category,
                severity=discovered.severity,
            )
        selected.decomposition_method = "deterministic_fallback"
        selected.semantic_clauses = list(plan.semantic_clauses)
        selected.ambiguities = list(dict.fromkeys(
            plan.ambiguities + ["Atomic LLM construction failed; automatic closure is disabled."]
        ))
        selected.contract_complete = False
        return selected

    normalized = _normalize_extracted_requirements(
        [candidate],
        source_by_code={discovered.req_code: source_text},
    )[0]
    best = normalized
    repairable_issues = _semantic_repair_issues(normalized.validation_issues)
    if repairable_issues and EXTRACTION_REPAIR_ATTEMPTS:
        print(
            f"    [Atomic repair] {discovered.req_code}: {len(repairable_issues)} semantic issue(s) "
            f"[{_format_validation_issue_categories(repairable_issues)}].",
            flush=True,
        )
        repair_prompt = f"""Repair one atomic requirement contract using only the listed validation issues.

Authoritative source:
{source_text}

Approved semantic plan:
{plan.model_dump()}

Previous contract:
{normalized.model_dump(exclude_none=True)}

Validation issues:
{repairable_issues}

Return exactly one complete corrected requirement. Preserve valid atoms and source meaning. Do not add an atom
unless it maps to an approved semantic clause. Use verbatim source_span values, precise source_parameter and
canonical_parameter values, complete clause_ids/clause_coverage, and a condition-ID nested logic_tree.
Set decomposition_method to staged. Return JSON only.

{ATOMIC_DECOMPOSITION_GUIDE}
"""
        repaired = await request(
            repair_prompt,
            (
                "You are repairing a source-grounded atomic contract. Correct only diagnosed structural or "
                "provenance defects and never invent an obligation. Return complete JSON only."
            ),
        )
        if repaired is not None:
            repaired = _normalize_extracted_requirements(
                [repaired],
                source_by_code={discovered.req_code: source_text},
            )[0]
            original_quality = (
                len(best.validation_issues), -int(best.contract_complete), -best.decomposition_confidence
            )
            repaired_quality = (
                len(repaired.validation_issues), -int(repaired.contract_complete), -repaired.decomposition_confidence
            )
            if repaired_quality < original_quality:
                best = repaired
            print(
                f"    [Atomic repair result] {discovered.req_code}: "
                f"{len(repaired.validation_issues)} issue(s) remain "
                f"[{_format_validation_issue_categories(repaired.validation_issues)}]; "
                f"{'accepted' if best is repaired else 'kept original'}.",
                flush=True,
            )
        else:
            print(
                f"    [Atomic repair result] {discovered.req_code}: repair response failed schema validation; "
                "kept original incomplete contract.",
                flush=True,
            )
    elif normalized.validation_issues:
        print(
            f"    [Atomic validation] {discovered.req_code}: "
            f"{len(normalized.validation_issues)} non-semantic issue(s) retained for review "
            f"[{_format_validation_issue_categories(normalized.validation_issues)}]; no LLM repair requested.",
            flush=True,
        )
    return best


async def _extract_chunk_staged(
    chunk_text: str,
    *,
    doc_name: str,
    active_model: str,
    thinking_level: Optional[str],
    atomic_model: Optional[str] = None,
    atomic_thinking_level: Optional[str] = None,
    atomic_fallback_model: Optional[str] = None,
    chunk_label: str,
    allow_rule_fallback: bool,
    allow_model_fallback: bool,
) -> list[ExtractedRequirement]:
    """Discover, plan, then construct each requirement independently."""
    discovered = await _discover_requirements(
        chunk_text,
        doc_name=doc_name,
        active_model=active_model,
        thinking_level=thinking_level,
        chunk_label=chunk_label,
        allow_rule_fallback=allow_rule_fallback,
        allow_model_fallback=allow_model_fallback,
    )
    if not discovered:
        return []
    active_atomic_model = atomic_model or active_model
    active_atomic_thinking = atomic_thinking_level or thinking_level
    source_by_code = {
        code: block
        for block in _requirement_blocks(chunk_text)
        if (code := _requirement_code_from_block(block)) is not None
    }
    discovered = [
        _ground_discovered_description(
            item,
            source_by_code.get(item.req_code, chunk_text),
        )
        for item in discovered
    ]
    plans = await _plan_requirement_clauses(
        discovered,
        chunk_text,
        doc_name=doc_name,
        active_model=active_atomic_model,
        fallback_model=atomic_fallback_model,
        thinking_level=active_atomic_thinking,
        chunk_label=chunk_label,
        allow_rule_fallback=allow_rule_fallback,
        allow_model_fallback=allow_model_fallback,
    )
    contracts: list[ExtractedRequirement] = []
    # The outer document-section semaphore bounds these calls globally. Keep
    # requirements sequential within a section to avoid endpoint bursts.
    for item in discovered:
        plan = plans[item.req_code]
        source = _discovered_source_block(item, source_by_code, chunk_text)
        contracts.append(await _construct_atomic_contract(
            item,
            plan,
            source,
            doc_name=doc_name,
            active_model=active_atomic_model,
            fallback_model=atomic_fallback_model,
            thinking_level=active_atomic_thinking,
            allow_rule_fallback=allow_rule_fallback,
            allow_model_fallback=allow_model_fallback,
        ))
    return _deduplicate_requirements(contracts)


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

Use canonical operators only: <, <=, ==, >=, >, or between. For every numeric condition, populate the
operator and threshold (or min_value plus max_value for between). For a qualitative required state,
populate operator == and use a boolean or concise expected-state threshold. Do not leave these fields
empty when the source states the comparison or expected state.

{ATOMIC_DECOMPOSITION_GUIDE}
"""


async def _retry_incomplete_contracts(
    source_text: str,
    requirements: list[ExtractedRequirement],
    *,
    doc_name: str,
    active_model: str,
    thinking_level: Optional[str],
    chunk_label: str,
    allow_model_fallback: bool = True,
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

{ATOMIC_DECOMPOSITION_GUIDE}
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
            allow_model_fallback=allow_model_fallback,
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
    allow_rule_fallback: bool = True,
    allow_model_fallback: bool = True,
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
            allow_model_fallback=allow_model_fallback,
        )
    except Exception as ex:
        import logging
        logging.getLogger("traceaudit.extraction").warning(
            "LLM extraction raised for %s at retry depth %s: %s",
            chunk_label,
            depth,
            ex,
        )

    if result is None and not allow_rule_fallback:
        raise RuntimeError(
            f"The requested extraction model {active_model!r} returned no valid structured response "
            f"for {chunk_label}; benchmark scoring was aborted instead of using rule fallback."
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
                allow_model_fallback=allow_model_fallback,
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
                allow_rule_fallback=allow_rule_fallback,
                allow_model_fallback=allow_model_fallback,
            )
        else:
            if not allow_rule_fallback:
                raise RuntimeError(
                    f"The requested extraction model {active_model!r} omitted source requirements "
                    f"after bounded retries for {chunk_label}; benchmark scoring was aborted."
                )
            recovered = _normalize_extracted_requirements(fallback_extract_requirements("\n".join(missing_blocks), doc_name))
        combined = _deduplicate_requirements(normalized + recovered)
        return await _retry_incomplete_contracts(
            chunk_text,
            combined,
            doc_name=doc_name,
            active_model=active_model,
            thinking_level=thinking_level,
            chunk_label=chunk_label,
            allow_model_fallback=allow_model_fallback,
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
                    allow_rule_fallback=allow_rule_fallback,
                    allow_model_fallback=allow_model_fallback,
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
    atomic_model: Optional[str] = None,
    atomic_thinking_level: Optional[str] = None,
    atomic_fallback_model: Optional[str] = None,
    allow_rule_fallback: bool = True,
    allow_model_fallback: bool = True,
) -> list[ExtractedRequirement]:
    """Extract structured requirements from document text using Gemini (with Thinking enabled) or Databricks/OpenAI.

    For large documents, explicit requirement boundaries are preserved and each
    bounded group is processed independently. Invalid/truncated output is retried
    on smaller groups before a local deterministic fallback is used. Requirement
    discovery uses ``model``; semantic planning and atomic contract construction
    use ``atomic_model`` with its own thinking level and explicit stage-local
    fallback.
    """
    active_model = model or settings.LLM_MODEL
    active_atomic_model = atomic_model or settings.ATOMIC_DECOMPOSITION_MODEL or active_model
    active_atomic_thinking = (
        atomic_thinking_level
        or settings.ATOMIC_DECOMPOSITION_THINKING_LEVEL
        or thinking_level
    )
    active_atomic_fallback = (
        settings.ATOMIC_DECOMPOSITION_FALLBACK_MODEL
        if atomic_fallback_model is None
        else atomic_fallback_model
    )
    has_keys = bool(
        settings.effective_gemini_api_key
        or settings.effective_databricks_token
        or settings.effective_groq_api_key
        or settings.effective_tokenrouter_api_key
        or settings.effective_openai_api_key
    )

    if not has_keys and not allow_rule_fallback:
        raise RuntimeError("No configured LLM credential is available for extraction benchmark mode.")
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
            requirements = await _extract_chunk_staged(
                chunk_text,
                doc_name=doc_name,
                active_model=active_model,
                thinking_level=thinking_level,
                atomic_model=active_atomic_model,
                atomic_thinking_level=active_atomic_thinking,
                atomic_fallback_model=active_atomic_fallback,
                chunk_label=chunk_label or "Section 1/1",
                allow_rule_fallback=allow_rule_fallback,
                allow_model_fallback=allow_model_fallback,
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

    if not allow_rule_fallback:
        raise RuntimeError(
            f"The requested extraction model {active_model!r} produced no requirements; "
            "benchmark scoring was aborted instead of using rule fallback."
        )
    return fallback_extract_requirements(text, doc_name)
