"""Structured Evidence Claim schema and extraction engine."""

from typing import Optional, Union, Literal
from pydantic import BaseModel, Field, model_validator
import re
import uuid

from app.schemas.contract import RequirementContract, RANGE_REGEX, THRESHOLD_LE_REGEX, THRESHOLD_GE_REGEX, IP_REGEX


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


def classify_source_authority(
    doc_name: str,
    content: str = "",
    doc_type: Optional[str] = None,
) -> SourceAuthority:
    """Classify the evidence source authority level consistently across the system."""
    dn = doc_name.lower()
    ct = content.lower()
    dt = (doc_type or "").lower()

    # 1. Compliance Matrix
    if "compliance matrix" in dt or "compliance_matrix" in dt or dn.endswith(".xlsx") or "matrix" in dn:
        return "COMPLIANCE_MATRIX"

    # 2. Simulation (SPICE, MATLAB, CFD, Simulink, etc.)
    if any(k in dn or k in ct for k in [
        "spice", "ltspice", "matlab", "simulink", "cfd", "simulation",
        "simulated", "model predicts", "modeling calculation", "theoretical simulation",
        "ansys", "finite element", "transient simulation"
    ]):
        if any(k in ct for k in ["spice", "matlab", "simulink", "cfd", "simulation", "simulated", "model predicts"]) or any(k in dn for k in ["spice", "matlab", "cfd", "simul"]):
            return "SIMULATION"

    # 3. Calculation / Analytical Estimation
    if any(k in dn or k in ct for k in [
        "calculation", "calculated", "analytical estimation", "formula predicts",
        "derivation", "theoretical calculation", "estimated by formula"
    ]) and not any(k in ct for k in ["measured", "tested", "lab test", "chamber", "dynamometer"]):
        return "CALCULATION"

    # 4. Architecture / Design Intent / Interface Spec
    if any(k in dn for k in ["02_system_architecture", "architecture", "arch_spec", "interface_spec", "system_architecture"]) or \
       any(k in ct for k in ["architecture section", "design intention", "is specified for", "architecture definition", "layout reviewed"]):
        return "ARCHITECTURE_SPEC"

    # 5. Supplier Component Datasheets
    if any(k in dn for k in ["03_bms_cell_supervisory_asic_datasheet", "datasheet", "data sheet", "ds-", "oem_supplier", "supplier", "component_datasheet"]):
        return "DATASHEET"

    # 6. Inspection
    if any(k in ct for k in ["visual inspection", "layout reviewed", "circuit layout reviewed", "schematic inspection"]):
        return "INSPECTION"

    # 7. Qualification / Validation / Empirical Test Reports
    if any(k in dn for k in [
        "validation_report", "test_report", "thermal_shock", "thermal_runaway",
        "environmental", "emc_report", "lab_report", "test_log"
    ]) or any(k in ct for k in [
        "tested at", "measured across", "injected at", "triggered in", "test points",
        "chamber", "oscilloscope", "dynamometer", "verdict: pass", "verdict: fail", "result: pass"
    ]):
        if "qualification" in dn or "thermal_shock" in dn:
            return "QUALIFICATION_TEST"
        if "validation" in dn:
            return "VALIDATION_REPORT"
        return "EMPIRICAL_TEST"

    return "UNKNOWN"


class EvidenceClaim(BaseModel):
    """A structured factual claim extracted from an evidence chunk."""

    claim_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    source_chunk_id: Optional[str] = None
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
    r"([+-]?\d+(?:\.\d+)?)\s*([°\w/µμ%]+)",
    re.IGNORECASE,
)

TEST_VERDICT_PATTERN = re.compile(
    r"\b(PASS|PASSED|FAIL|FAILED|NOT\s+TESTED|IN\s+PROGRESS|PARTIAL|MISSING|COMPLETED)\b",
    re.IGNORECASE,
)


def extract_claims_from_chunk(
    chunk: dict,
    contract: Optional[RequirementContract] = None,
) -> list[EvidenceClaim]:
    """Extract one or more typed EvidenceClaims from a single evidence chunk."""
    text = chunk.get("quote") or chunk.get("content") or ""
    doc_name = chunk.get("document_name", "Document")
    doc_type = chunk.get("doc_type")
    page_num = chunk.get("page_number")
    chunk_id = chunk.get("chunk_id") or chunk.get("id")
    claims: list[EvidenceClaim] = []
    text_lower = text.lower()

    source_auth = classify_source_authority(doc_name, text, doc_type)

    # Determine entity scope of chunk
    entity_scope = "System"
    if "asic" in text_lower or "asic" in doc_name.lower():
        entity_scope = "ASIC"
    elif "inverter" in text_lower or "inverter" in doc_name.lower() or "gate driver" in text_lower:
        entity_scope = "Inverter"
    elif "bcu" in text_lower or "bcu" in doc_name.lower():
        entity_scope = "BCU"
    elif "bms" in text_lower or "bms" in doc_name.lower():
        entity_scope = "BMS"
    elif "pack" in text_lower or "pack" in doc_name.lower():
        entity_scope = "Pack"
    elif "hvil" in text_lower or "hvil" in doc_name.lower():
        entity_scope = "HVIL"
    elif "dc-dc" in text_lower or "dcdc" in text_lower or "dc-dc" in doc_name.lower():
        entity_scope = "DC-DC"

    # If the chunk explicitly lists requirement codes, ensure it applies to this contract
    target_text = text
    if contract and contract.req_code:
        req_codes_in_text = [code.upper() for code in REQ_CODE_REGEX.findall(text)]
        if req_codes_in_text:
            if contract.req_code.upper() not in req_codes_in_text:
                return []
            # Scope extraction to the specific section for contract.req_code
            pos = text.upper().find(contract.req_code.upper())
            start_pos = max(0, pos - 50)
            next_req = REQ_CODE_REGEX.search(text[pos + len(contract.req_code):])
            end_pos = (pos + len(contract.req_code) + next_req.start()) if next_req else min(len(text), pos + 600)
            target_text = text[start_pos:end_pos]

    target_text_lower = target_text.lower()

    # 1. Extract explicit test verdict claims (especially from matrices and lab reports)
    verdict_match = TEST_VERDICT_PATTERN.search(target_text)
    if verdict_match or any(k in target_text_lower for k in ["not started", "verdict: missing", "record missing", "[missing evidence]", "in progress"]):
        verdict_str = "MISSING" if any(k in target_text_lower for k in ["not started", "verdict: missing", "record missing", "[missing evidence]"]) else ("IN PROGRESS" if "in progress" in target_text_lower else verdict_match.group(1).upper())
        normalized_verdict = {
            "PASSED": "PASS",
            "FAILED": "FAIL",
            "COMPLETED": "PASS",
            "NOT TESTED": "NOT TESTED",
            "IN PROGRESS": "IN PROGRESS",
        }.get(verdict_str, verdict_str)

        claims.append(EvidenceClaim(
            source_chunk_id=chunk_id,
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
            min_v = float(m.group(1))
            unit_pre = m.group(2)
            max_v = float(m.group(3))
            unit_post = m.group(4)
            unit = (unit_post or unit_pre or "").strip()

            claims.append(EvidenceClaim(
                source_chunk_id=chunk_id,
                document_name=doc_name,
                page_number=page_num,
                claim_type="numeric_range",
                source_authority=source_auth,
                entity_scope=entity_scope,
                min_value=min_v,
                max_value=max_v,
                unit=unit or None,
                quote=text[max(0, m.start() - 40):min(len(text), m.end() + 40)],
            ))
        except (ValueError, TypeError):
            continue

    # 3. Extract numbers grouped by unit with local context snippets
    points_by_unit: dict[str, list[tuple[float, str]]] = {}
    for m in NUM_WITH_UNIT_PATTERN.finditer(target_text):
        try:
            val = float(m.group(1))
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
                    document_name=doc_name,
                    page_number=page_num,
                    claim_type="discrete_sweep",
                    source_authority=source_auth,
                    entity_scope=entity_scope,
                    discrete_points=[p[0] for p in pts],
                    unit=u,
                    quote=" ".join(p[1] for p in pts),
                ))
            elif len(pts) == 1:
                claims.append(EvidenceClaim(
                    source_chunk_id=chunk_id,
                    document_name=doc_name,
                    page_number=page_num,
                    claim_type="threshold",
                    source_authority=source_auth,
                    entity_scope=entity_scope,
                    value=pts[0][0],
                    unit=u,
                    quote=pts[0][1],
                ))
    else:
        for u, pts in points_by_unit.items():
            if len(pts) == 1:
                claims.append(EvidenceClaim(
                    source_chunk_id=chunk_id,
                    document_name=doc_name,
                    page_number=page_num,
                    claim_type="threshold",
                    source_authority=source_auth,
                    entity_scope=entity_scope,
                    value=pts[0][0],
                    unit=u,
                    quote=pts[0][1],
                ))

    # 4. Extract IP rating claim
    ip_m = IP_REGEX.search(target_text)
    if ip_m:
        claims.append(EvidenceClaim(
            source_chunk_id=chunk_id,
            document_name=doc_name,
            page_number=page_num,
            claim_type="boolean",
            source_authority=source_auth,
            entity_scope=entity_scope,
            parameter="ingress_protection",
            value=ip_m.group(1).upper(),
            unit="IP",
            test_result="PASS" if "pass" in target_text_lower or "zero water" in target_text_lower else "UNKNOWN",
            quote=target_text,
        ))

    # 5. Fallback generic semantic claim if no structured claim was parsed
    if not claims:
        claims.append(EvidenceClaim(
            source_chunk_id=chunk_id,
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
