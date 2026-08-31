"""Structured Evidence Claim schema and extraction engine."""

from typing import Any, Optional, Union, Literal
from pydantic import BaseModel, Field, model_validator
import re
import uuid

from app.schemas.contract import RequirementContract, RANGE_REGEX, THRESHOLD_LE_REGEX, THRESHOLD_GE_REGEX, IP_REGEX
from app.schemas.evidence_qualification import (
    extract_parameters_from_text,
    normalize_entity_scope,
    normalize_parameter,
)
from app.services.units import (
    UnitCompatibility,
    convert_value,
    normalize_unit_str,
    unit_compatibility,
)


REQ_CODE_REGEX = re.compile(r"\b(REQ[-_]?[A-Za-z0-9_-]*\d+)\b", re.IGNORECASE)


ClaimType = Literal[
    "numeric_range",
    "threshold",
    "discrete_sweep",
    "test_verdict",
    "boolean",
    "semantic",
    "unknown",
]

SourceAuthority = Literal[
    "EMPIRICAL_TEST",
    "QUALIFICATION_TEST",
    "VALIDATION_REPORT",
    "COMPLIANCE_MATRIX",
    "DATASHEET",
    "ARCHITECTURE_SPEC",
    "SIMULATION",
    "CALCULATION",
    "INSPECTION",
    "UNKNOWN",
]


_PROFILE_ROLE_TO_AUTHORITY: dict[str, SourceAuthority] = {
    "TEST_REPORT": "EMPIRICAL_TEST",
    "DATASHEET": "DATASHEET",
    "COMPLIANCE_MATRIX": "COMPLIANCE_MATRIX",
    "ARCHITECTURE": "ARCHITECTURE_SPEC",
    # Normative specifications define the obligation; they are not empirical
    # proof. ARCHITECTURE_SPEC keeps them on the design/non-test side of the
    # existing authority taxonomy when they are not excluded upstream.
    "SPECIFICATION": "ARCHITECTURE_SPEC",
}


def _profile_value(profile: Optional[dict[str, Any]], key: str) -> Any:
    if not profile:
        return None
    if isinstance(profile, dict):
        return profile.get(key)
    return getattr(profile, key, None)


def classify_source_authority(
    doc_name: str,
    content: str = "",
    doc_type: Optional[str] = None,
    document_profile: Optional[dict[str, Any]] = None,
) -> SourceAuthority:
    """Classify stable document authority, independently of passage modality."""
    dn = doc_name.lower()
    ct = content.lower()
    dt = (doc_type or "").lower()

    # A content-derived document profile is the strongest identity signal. It
    # is produced once per document and includes auditable page/quote evidence.
    profiled_role = str(_profile_value(document_profile, "primary_role") or "").upper()
    profiled_authority = _PROFILE_ROLE_TO_AUTHORITY.get(profiled_role)
    if profiled_authority:
        return profiled_authority

    # Explicit/dynamically updated document types are the next signal. Unlike
    # filename matching this works for arbitrary names such as 305-2400152.pdf.
    if "test report" in dt or "laboratory report" in dt:
        return "EMPIRICAL_TEST"
    if "validation report" in dt:
        return "VALIDATION_REPORT"
    if "qualification" in dt:
        return "QUALIFICATION_TEST"
    if "supplier" in dt or "datasheet" in dt:
        return "DATASHEET"
    if "architecture" in dt or "technical specification" in dt or "regulatory specification" in dt:
        return "ARCHITECTURE_SPEC"

    # 1. Compliance Matrix
    if "compliance matrix" in dt or "compliance_matrix" in dt or dn.endswith(".xlsx") or "matrix" in dn:
        return "COMPLIANCE_MATRIX"

    # 2. Passage modality overrides a generic report filename. Treat explicit
    # modelling tools/phrases as simulation, but do not let the ambiguous word
    # "simulation" erase physical-test actions such as voltage being applied
    # and degradation being observed (for example, a physical scenario test
    # whose title happens to contain the word "simulation").
    strong_simulation_markers = (
        "spice", "ltspice", "matlab", "simulink", "cfd", "model predicts",
        "modeling calculation", "theoretical simulation", "ansys",
        "finite element", "transient simulation", "simulation records",
        "analytical calculations", "analytical modelling", "analytical modeling",
        "modeling:", "modelling:",
    )
    physical_action_markers = (
        " applied ", " measured ", " observed ", " recorded ", " injected ",
        "test case", "verdict: pass", "verdict: fail", "no degradation",
        "no thermal runaway", "bench test", "lab test", "chamber test",
    )
    has_strong_simulation = any(k in dn or k in ct for k in strong_simulation_markers)
    has_ambiguous_simulation = any(k in ct for k in ("simulation", "simulated"))
    has_physical_action = any(k in f" {ct} " for k in physical_action_markers)
    if has_strong_simulation or (
        has_ambiguous_simulation and not has_physical_action
    ) or any(k in dn for k in ("spice", "matlab", "cfd", "simul")):
        return "SIMULATION"

    # 3. Supplier component datasheets (document-level identity).
    if any(k in dn for k in ["datasheet", "data sheet", "ds-", "oem_supplier", "supplier", "component_datasheet", "part_spec", "ic_spec"]):
        return "DATASHEET"

    # 4. Architecture documents (document-level identity).
    if any(k in dn for k in ["architecture", "arch_spec", "interface_spec", "system_architecture", "design_intent", "system_design", "block_diagram"]):
        return "ARCHITECTURE_SPEC"

    # 5. Calculation / Analytical Estimation
    if any(k in dn or k in ct for k in [
        "calculation", "calculated", "analytical estimation", "formula predicts",
        "derivation", "theoretical calculation", "estimated by formula"
    ]) and not any(k in ct for k in ["measured", "tested", "lab test", "chamber", "dynamometer"]):
        return "CALCULATION"

    # 6. Inspection-only passage
    if any(k in ct for k in ["visual inspection", "layout reviewed", "circuit layout reviewed", "schematic inspection"]):
        return "INSPECTION"

    # 7. Qualification / Validation / Empirical Test Reports. Filename role is
    # evaluated before incidental architecture words in another page section.
    if any(k in dn for k in [
        "validation_report", "validation", "test_report", "test report", "_report",
        "thermal_shock", "thermal_runaway", "environmental", "emc_report", "lab_report", "test_log"
    ]) or any(k in ct for k in [
        "tested at", "measured across", "injected at", "triggered in", "test points",
        "chamber", "oscilloscope", "dynamometer", "verdict: pass", "verdict: fail", "result: pass"
    ]):
        if "qualification" in dn or "thermal_shock" in dn:
            return "QUALIFICATION_TEST"
        if "validation" in dn:
            return "VALIDATION_REPORT"
        return "EMPIRICAL_TEST"

    # 8. Content-only document roles, used when filenames are uninformative.
    if any(k in ct for k in ["absolute maximum ratings", "electrical characteristics", "pin configuration", "package dimensions", "typical application circuit"]):
        return "DATASHEET"
    if any(k in ct for k in ["architecture section", "design intention", "is specified for", "architecture definition", "layout reviewed"]):
        return "ARCHITECTURE_SPEC"

    return "UNKNOWN"


def classify_passage_modality(
    content: str,
    document_profile: Optional[dict[str, Any]] = None,
) -> str:
    """Classify what a passage says without rewriting document authority."""
    lower = f" {content.lower()} "
    profile_basis = str(_profile_value(document_profile, "verification_basis") or "").lower()
    calculation_markers = (
        "calculation", "calculated", "equation", "formula", "derived", "ri/vb",
    )
    physical_markers = (
        "measured", "observed", "recorded", "test result", "data sheet no.",
        "impact test", "rollover", "test date", "passed", "failed",
    )
    inspection_markers = (
        "visual inspection", "photograph", "photo no.", "observed visually",
    )
    strong_simulation_markers = (
        "finite element", "matlab", "simulink", "cfd", "spice", "model predicts",
        "analytical calculations", "simulation records", "theoretical simulation",
    )

    has_calculation = any(marker in lower for marker in calculation_markers)
    has_physical = any(marker in lower for marker in physical_markers)
    if any(marker in lower for marker in inspection_markers):
        return "inspection"
    if any(marker in lower for marker in strong_simulation_markers):
        return "simulation"
    if any(marker in lower for marker in ("simulation", "simulated")) and not has_physical:
        return "simulation"
    if has_calculation and (has_physical or profile_basis == "physical_test"):
        return "derived_test_calculation"
    if has_calculation:
        return "calculation"
    if has_physical or profile_basis == "physical_test":
        return "physical_test"
    if re.search(r"\bshall\b|\bmust\b|\brequired\s+to\b", lower):
        return "normative_statement"
    return "unknown"


class EvidenceClaim(BaseModel):
    """A structured factual claim extracted from an evidence chunk."""

    claim_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_chunk_id: Optional[str] = None
    requirement_id: Optional[str] = None
    condition_id: Optional[str] = None
    document_name: str
    page_number: Optional[int] = None
    claim_type: ClaimType = "unknown"
    source_authority: SourceAuthority = "UNKNOWN"
    entity_scope: Optional[str] = None  # "BCU", "ASIC", "Inverter", "System"
    parameter: Optional[str] = None
    value: Optional[Union[float, str, bool]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    discrete_points: list[float] = Field(default_factory=list)
    unit: Optional[str] = None
    test_method: Optional[str] = None
    test_result: Optional[str] = None  # "PASS", "FAIL", "PARTIAL", "IN PROGRESS", "NOT TESTED", "MISSING", "UNKNOWN"
    quote: str
    context: Optional[str] = None

    @model_validator(mode="after")
    def populate_source_authority(self) -> "EvidenceClaim":
        if self.source_authority == "UNKNOWN" and self.document_name:
            self.source_authority = classify_source_authority(self.document_name, self.quote)
        return self



# Patterns for extracting measured test results
TESTED_SWEEP_PATTERN = re.compile(
    r"(?:tested\s+(?:at|across|with)|evaluated\s+at)\s+([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?(?:\s*,\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?)*(?:\s*(?:and|&)\s*([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)?)?",
    re.IGNORECASE,
)

NUM_WITH_UNIT_PATTERN = re.compile(
    r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([°\w/µμ%]+)",
    re.IGNORECASE,
)

TEST_VERDICT_PATTERN = re.compile(
    r"\b(PASS|PASSED|FAIL|FAILED|NOT\s+TESTED|IN\s+PROGRESS|PARTIAL|MISSING|COMPLETED)\b",
    re.IGNORECASE,
)


def _dominant_test_verdict(text: str) -> Optional[str]:
    """Return the most safety-significant local verdict expressed in text.

    A passage may legitimately contain both a sub-test PASS and a decisive
    requirement-level FAIL (for example IP6X dust passed but IP67 water ingress
    failed). Negative or incomplete outcomes therefore dominate positive words.
    """
    lower = text.lower()
    if any(k in lower for k in ("not started", "verdict: missing", "record missing", "[missing evidence]", "not tested")):
        return "MISSING" if "not tested" not in lower else "NOT TESTED"

    normalized = []
    for match in TEST_VERDICT_PATTERN.finditer(text):
        raw = match.group(1).upper()
        normalized.append({
            "PASSED": "PASS",
            "FAILED": "FAIL",
            "COMPLETED": "PASS",
            "NOT TESTED": "NOT TESTED",
            "IN PROGRESS": "IN PROGRESS",
        }.get(raw, raw))

    if "FAIL" in normalized:
        return "FAIL"
    if "IN PROGRESS" in normalized or "PARTIAL" in normalized or "in progress" in lower:
        return "IN PROGRESS"
    if "MISSING" in normalized or "NOT TESTED" in normalized:
        return "MISSING"
    if "PASS" in normalized:
        return "PASS"
    return None

_PASSAGE_STOPWORDS = {
    "requirement", "system", "shall", "must", "with", "within", "from", "that",
    "this", "every", "hardware", "test", "testing", "verification", "maximum",
    "minimum", "provide", "support", "operation", "operating", "complete", "peak",
    "current", "voltage", "temperature", "frequency", "resistance", "power", "range",
    "latency", "duration", "time", "threshold", "margin", "error", "value", "condition",
}


def _isolate_relevant_passage(text: str, contract: Optional[RequirementContract]) -> str:
    """Return only lines that locally address the target requirement.

    Parsed PDF pages often contain several unrelated test cases. Keeping the
    whole page lets a PASS or numeric value from one case contaminate another.
    """
    if not contract or not text.strip():
        return text

    code = contract.req_code.upper()
    codes_in_text = [match.upper() for match in REQ_CODE_REGEX.findall(text)]
    if codes_in_text:
        if code not in codes_in_text:
            return ""
        pos = text.upper().find(code)
        # Never carry the tail of the preceding matrix row into this
        # requirement's claims (it may contain another row's PASS/FAIL).
        start_pos = pos
        next_req = REQ_CODE_REGEX.search(text[pos + len(code):])
        end_pos = (pos + len(code) + next_req.start()) if next_req else min(len(text), pos + 900)
        return text[start_pos:end_pos]

    suffix_match = re.search(r"(\d+)$", code)
    suffix = suffix_match.group(1) if suffix_match else ""
    direct_id = re.compile(rf"\b(?:TC|TEST(?:\s+CASE)?)[-_][A-Z0-9_-]*[-_]{re.escape(suffix)}\b", re.IGNORECASE) if suffix else None
    if direct_id:
        direct_match = direct_id.search(text)
        if direct_match:
            # PDF test cases often wrap onto following lines. Keep the full
            # case until the next TC marker instead of just the first line.
            next_case = re.search(
                r"\b(?:TC|TEST(?:\s+CASE)?)[-_][A-Z0-9_-]*[-_]\d+\b",
                text[direct_match.end():],
                re.IGNORECASE,
            )
            end_pos = direct_match.end() + next_case.start() if next_case else min(len(text), direct_match.start() + 1000)
            start_pos = max(0, text.rfind("\n", 0, direct_match.start()) + 1)
            return text[start_pos:end_pos].strip()
    term_source = " ".join(
        [contract.title, contract.raw_text]
        + [c.description or "" for c in contract.atomic_conditions]
        + [c.parameter or "" for c in contract.atomic_conditions]
    ).replace("_", " ").replace("-", " ")
    terms = {
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", term_source)
        if word.lower() not in _PASSAGE_STOPWORDS
    }
    if "range" in term_source.lower():
        terms.add("envelope")

    selected: list[str] = []
    required_overlap = min(3 if len(terms) >= 6 else 2, len(terms))
    for line in text.splitlines():
        line_lower = line.lower()
        overlap = sum(1 for term in terms if term in line_lower)
        if required_overlap and overlap >= required_overlap:
            selected.append(line.strip())

    # PDF tables frequently extract one cell per line. Score compact sliding
    # windows so a parameter, rollover stage, value, unit, and verdict can be
    # kept together instead of each isolated cell failing the line threshold.
    if not selected and terms:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        best_window: list[str] = []
        best_overlap = 0
        window_size = min(18, max(8, len(lines)))
        for start in range(len(lines)):
            window = lines[start:start + window_size]
            joined = " ".join(window).lower()
            overlap = sum(1 for term in terms if term in joined)
            if overlap > best_overlap:
                best_overlap = overlap
                best_window = window
        if best_overlap >= required_overlap:
            selected = best_window

    if not selected and not terms and len(text) <= 300 and re.search(r"\d", text) and any(
        marker in text.lower() for marker in ("tested", "measured", "verified", "verdict", "evaluated")
    ):
        return text
    return "\n".join(selected)


def _infer_claim_parameter(snippet: str, unit: Optional[str]) -> Optional[str]:
    """Infer a claim parameter from its local words and physical unit."""
    found = extract_parameters_from_text(snippet)
    normalized_unit = normalize_unit_str(unit)
    preferred_by_unit = {
        "db": "emissions_margin",
        "hz": "frequency", "khz": "frequency", "mhz": "frequency", "ghz": "frequency",
        "ns": "latency", "us": "latency", "ms": "latency", "s": "latency",
        "v": "voltage", "vdc": "voltage", "v dc": "voltage", "mv": "voltage", "kv": "voltage",
        "a": "current", "ma": "current", "ua": "current", "ka": "current",
        "w": "heat_load", "kw": "heat_load",
    }
    preferred = preferred_by_unit.get(normalized_unit)
    if preferred:
        return preferred
    return normalize_parameter(found[0]) if found else None


def _resolve_range_unit_capture(unit: Optional[str]) -> str:
    """Remove regex-captured prose only when Pint validates the shorter unit.

    ``RANGE_REGEX`` permits a second word for legitimate units such as
    ``V DC``. In ordinary sentences that slot can instead capture the next
    word (``GHz during``). The unit parser, rather than a vocabulary of prose
    words, decides whether the complete or shortened expression is valid.
    """
    candidate = (unit or "").strip()
    if not candidate or normalize_unit_str(candidate):
        return candidate
    pieces = candidate.split()
    while len(pieces) > 1:
        pieces.pop()
        shortened = " ".join(pieces)
        if normalize_unit_str(shortened):
            return shortened
    return candidate


_NON_OBSERVED_VALUE_MARKERS = (
    "pending", "planned", "scheduled", "not tested", "not yet tested",
    "not yet verified", "to be tested", "target", "required", "requirement",
    "shall", "remaining", "future", "TBD",
)
_OBSERVED_VALUE_MARKERS = (
    "tested", "measured", "observed", "recorded", "achieved", "actual",
    "completed", "verified", "operated", "reached", "result",
)


def _numeric_value_is_non_observed(text: str, start: int, end: int) -> bool:
    """Reject target/planned numbers before they become measured claims.

    The check is deliberately clause-local so an observed value in one
    semicolon-separated clause does not turn a pending target in another
    clause into an observation.
    """
    left = max(text.rfind(";", 0, start), text.rfind("\n", 0, start), text.rfind(".", 0, start))
    right_candidates = [pos for pos in (text.find(";", end), text.find("\n", end), text.find(".", end)) if pos >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    clause = text[left + 1:right].lower()
    has_non_observed = any(marker.lower() in clause for marker in _NON_OBSERVED_VALUE_MARKERS)
    has_observed = any(marker in clause for marker in _OBSERVED_VALUE_MARKERS)
    # Explicit negated/pending modality wins even if the phrase contains the
    # token "tested" (for example, "not yet tested to 20,000 rpm").
    explicit_negative = any(marker in clause for marker in (
        "pending", "planned", "scheduled", "not tested", "not yet",
        "to be tested", "remaining", "future", "tbd",
    ))
    return explicit_negative or (has_non_observed and not has_observed)


def extract_claims_from_chunk(
    chunk: dict,
    contract: Optional[RequirementContract] = None,
) -> list[EvidenceClaim]:
    """Extract one or more typed EvidenceClaims from a single evidence chunk."""
    text = chunk.get("quote") or chunk.get("content") or ""
    doc_name = chunk.get("document_name", "Document")
    doc_type = chunk.get("doc_type")
    document_profile = chunk.get("document_profile")
    page_num = chunk.get("page_number")
    chunk_id = chunk.get("chunk_id") or chunk.get("id")
    claims: list[EvidenceClaim] = []
    target_text = _isolate_relevant_passage(text, contract)
    if not target_text.strip():
        return []

    # Normalize the two common replacement-character artifacts produced by
    # PDF extraction: micro units (�s/�A) and an en-dash between range bounds.
    target_text = re.sub(r"�(?=[sSaA]\b)", "u", target_text)
    target_text = re.sub(r"(?<=[A-Za-z])�(?=[+-]?\d)", " to ", target_text)

    source_auth = classify_source_authority(
        doc_name,
        target_text,
        doc_type,
        document_profile=document_profile,
    )

    # Determine entity scope from the local passage, not the complete page.
    entity_scope = normalize_entity_scope(target_text, doc_name)

    target_text_lower = target_text.lower()

    # 1. Extract explicit test verdict claims (especially from matrices and lab reports)
    normalized_verdict = _dominant_test_verdict(target_text)
    if normalized_verdict:
        claims.append(EvidenceClaim(
            source_chunk_id=chunk_id,
            requirement_id=contract.req_code if contract else None,
            document_name=doc_name,
            page_number=page_num,
            claim_type="test_verdict",
            source_authority=source_auth,
            entity_scope=entity_scope,
            test_result=normalized_verdict,
            quote=target_text,
        ))

    # 2. Extract numeric range claims (e.g. "tested from -20.0 °C to +70.0 °C", "400.0 V to 750.0 V DC")
    for m in RANGE_REGEX.finditer(target_text):
        try:
            if _numeric_value_is_non_observed(target_text, m.start(), m.end()):
                continue
            min_v = float(m.group(1))
            unit_pre = _resolve_range_unit_capture(m.group(2))
            max_v = float(m.group(3))
            unit_post = _resolve_range_unit_capture(m.group(4))
            unit = (unit_post or unit_pre or "").strip()
            # A range claim can carry only one unit, so merge mixed-unit
            # endpoints only when the standards unit engine proves that they
            # have the same dimensionality. Unknown or incompatible pairs stay
            # available to the semantic LLM as passage text, but are not turned
            # into a misleading deterministic numeric claim.
            if unit_pre and unit_post:
                compatibility = unit_compatibility(unit_pre, unit_post)
                if compatibility != UnitCompatibility.COMPATIBLE:
                    continue
                if normalize_unit_str(unit_pre) != normalize_unit_str(unit_post):
                    range_condition = next(
                        (
                            c for c in (contract.atomic_conditions if contract else [])
                            if c.min_value is not None and c.max_value is not None and c.unit
                        ),
                        None,
                    )
                    target_unit = range_condition.unit if range_condition else unit_post
                    converted_min = convert_value(min_v, unit_pre, target_unit)
                    converted_max = convert_value(max_v, unit_post, target_unit)
                    if converted_min is None or converted_max is None:
                        continue
                    min_v, max_v, unit = converted_min, converted_max, target_unit

            quote = target_text[max(0, m.start() - 40):min(len(target_text), m.end() + 40)]

            claims.append(EvidenceClaim(
                source_chunk_id=chunk_id,
                requirement_id=contract.req_code if contract else None,
                document_name=doc_name,
                page_number=page_num,
                claim_type="numeric_range",
                source_authority=source_auth,
                entity_scope=entity_scope,
                parameter=_infer_claim_parameter(quote, unit),
                min_value=min_v,
                max_value=max_v,
                unit=unit or None,
                quote=quote,
            ))
        except (ValueError, TypeError):
            continue

    # 3. Extract numbers grouped by unit with local context snippets
    points_by_unit: dict[str, list[tuple[float, str]]] = {}
    for m in NUM_WITH_UNIT_PATTERN.finditer(target_text):
        try:
            if _numeric_value_is_non_observed(target_text, m.start(), m.end()):
                continue
            val = float(m.group(1).replace(",", ""))
            u = m.group(2).strip()
            if u and len(u) <= 8 and not any(ch.isdigit() for ch in u):
                snippet = target_text[max(0, m.start() - 60):min(len(target_text), m.end() + 60)]
                points_by_unit.setdefault(u, []).append((val, snippet))
        except (ValueError, TypeError):
            continue

    if any(tw in target_text_lower for tw in ["tested", "measured", "evaluated", "points", "completed", "verified", "observed"]):
        for u, pts in points_by_unit.items():
            if len(pts) >= 2:
                claims.append(EvidenceClaim(
                    source_chunk_id=chunk_id,
                    requirement_id=contract.req_code if contract else None,
                    document_name=doc_name,
                    page_number=page_num,
                    claim_type="discrete_sweep",
                    source_authority=source_auth,
                    entity_scope=entity_scope,
                    parameter=_infer_claim_parameter(" ".join(p[1] for p in pts), u),
                    discrete_points=[p[0] for p in pts],
                    unit=u,
                    quote=" ".join(p[1] for p in pts),
                ))
            elif len(pts) == 1:
                claims.append(EvidenceClaim(
                    source_chunk_id=chunk_id,
                    requirement_id=contract.req_code if contract else None,
                    document_name=doc_name,
                    page_number=page_num,
                    claim_type="threshold",
                    source_authority=source_auth,
                    entity_scope=entity_scope,
                    parameter=_infer_claim_parameter(pts[0][1], u),
                    value=pts[0][0],
                    unit=u,
                    quote=pts[0][1],
                ))
    else:
        for u, pts in points_by_unit.items():
            if len(pts) == 1:
                claims.append(EvidenceClaim(
                    source_chunk_id=chunk_id,
                    requirement_id=contract.req_code if contract else None,
                    document_name=doc_name,
                    page_number=page_num,
                    claim_type="threshold",
                    source_authority=source_auth,
                    entity_scope=entity_scope,
                    parameter=_infer_claim_parameter(pts[0][1], u),
                    value=pts[0][0],
                    unit=u,
                    quote=pts[0][1],
                ))

    # 4. Extract IP rating claim
    ip_matches = list(IP_REGEX.finditer(target_text))
    if ip_matches:
        observed_ip = ip_matches[-1].group(1).upper()
        ip_failure = any(k in target_text_lower for k in (
            "water ingress", "leakage", "leak detected", "verdict: fail", " failed",
        )) or bool(re.search(r"\brated\s+as\s+ip\d{2}[a-z]?\s+only\b", target_text_lower))
        claims.append(EvidenceClaim(
            source_chunk_id=chunk_id,
            requirement_id=contract.req_code if contract else None,
            document_name=doc_name,
            page_number=page_num,
            claim_type="boolean",
            source_authority=source_auth,
            entity_scope=entity_scope,
            parameter="ingress_protection",
            value=observed_ip,
            unit="IP",
            test_result=(
                "FAIL" if ip_failure
                else "PASS" if "pass" in target_text_lower or "zero water" in target_text_lower
                else "UNKNOWN"
            ),
            quote=target_text,
        ))

    # 5. Fallback generic semantic claim if no structured claim was parsed
    if not claims:
        claims.append(EvidenceClaim(
            source_chunk_id=chunk_id,
            requirement_id=contract.req_code if contract else None,
            document_name=doc_name,
            page_number=page_num,
            claim_type="semantic",
            source_authority=source_auth,
            entity_scope=entity_scope,
            quote=text,
        ))

    return claims



def extract_all_evidence_claims(
    candidate_chunks: list[dict],
    contract: Optional[RequirementContract] = None,
) -> list[EvidenceClaim]:
    """Extract structured claims across all candidate evidence chunks."""
    all_claims = []
    for chunk in candidate_chunks:
        claims = extract_claims_from_chunk(chunk, contract)
        all_claims.extend(claims)
    return all_claims
