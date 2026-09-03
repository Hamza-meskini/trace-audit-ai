"""Evidence Qualification service.

Runs BEFORE verification: decides whether each piece of retrieved evidence
is allowed to prove (or refute) a requirement's conditions, across four
axes: source authority, verification method, entity scope, and parameter.

Single qualification path for the whole system — the reasoner, the
deterministic fallback, and the LLM prompts all consume qualifications
produced here. Never duplicate these rules elsewhere.
"""

import logging
from typing import Any, Optional

from app.schemas.claim import (
    EvidenceClaim,
    _isolate_relevant_passage,
    classify_passage_modality,
    classify_source_authority,
)
from app.schemas.contract import RequirementContract
from app.schemas.evidence_qualification import (
    AUTHORITY_TO_METHOD,
    EvidenceQualification,
    extract_parameters_from_text,
    methods_compatible,
    normalize_entity_scope,
    normalize_parameter,
    normalize_required_method,
    parameters_compatible,
    scopes_compatible,
)

logger = logging.getLogger("traceaudit.qualification")

# Authorities that constitute authoritative verification records
AUTHORITATIVE_AUTHORITIES = (
    "EMPIRICAL_TEST",
    "QUALIFICATION_TEST",
    "VALIDATION_REPORT",
    "COMPLIANCE_MATRIX",
)


def _contract_parameters(contract: RequirementContract) -> list[str]:
    params: list[str] = []
    for value in [contract.parameter, *[c.parameter for c in contract.atomic_conditions]]:
        normalized = normalize_parameter(value)
        if normalized and normalized not in params:
            params.append(normalized)
    return params


def _profile_field(profile: Optional[dict[str, Any]], key: str, default: Any = None) -> Any:
    if not profile:
        return default
    return profile.get(key, default) if isinstance(profile, dict) else getattr(profile, key, default)


def _evidence_method(authority: str, passage_modality: str) -> str:
    """Derive method from the passage while retaining stable document identity."""
    if passage_modality in {"physical_test", "derived_test_calculation"}:
        return "physical_test"
    if passage_modality in {"simulation", "calculation", "inspection"}:
        return passage_modality
    return AUTHORITY_TO_METHOD.get(authority, "unknown")


def qualify_evidence(
    contract: RequirementContract,
    evidence_id: str,
    document_name: str,
    content: str,
    doc_type: Optional[str] = None,
    evidence_parameter: Optional[str] = None,
    source_chunk_id: Optional[str] = None,
    document_profile: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> EvidenceQualification:
    """Qualify one evidence unit against one requirement contract."""
    local_content = _isolate_relevant_passage(content, contract, metadata)
    document_role = _profile_field(document_profile, "primary_role")
    profile_confidence = _profile_field(document_profile, "confidence")
    profile_requires_review = bool(_profile_field(document_profile, "requires_review", False))
    if not local_content.strip():
        # Keep conservative scope/parameter facts from the raw passage for
        # contradiction screening. The passage remains NOT_QUALIFIED for
        # proof, but a matching ASIC hard limit may still refute an ASIC
        # requirement while the same limit must not refute a BCU requirement.
        raw_authority = classify_source_authority(
            document_name,
            content,
            doc_type,
            document_profile=document_profile,
        )
        raw_modality = classify_passage_modality(content, document_profile)
        raw_method = _evidence_method(raw_authority, raw_modality)
        raw_scope = normalize_entity_scope(content, document_name)
        raw_params = extract_parameters_from_text(content)
        required_params = _contract_parameters(contract)
        raw_checks = [
            parameters_compatible(required, observed, content)
            for required in required_params
            for observed in (raw_params or [None])
        ]
        if any(check is True for check in raw_checks):
            raw_param_ok = True
        elif raw_checks and all(check is False for check in raw_checks):
            raw_param_ok = False
        else:
            raw_param_ok = None
        return EvidenceQualification(
            evidence_id=evidence_id,
            source_chunk_id=source_chunk_id,
            document_name=document_name,
            document_role=document_role,
            document_profile_confidence=profile_confidence,
            source_authority=raw_authority,
            passage_modality=raw_modality,
            relevance_status="NOT_RELEVANT",
            entity_scope=raw_scope,
            parameter=raw_params[0] if raw_params else None,
            parameters_found=raw_params,
            verification_method=raw_method,
            required_verification_method=contract.verification_method,
            method_compatible=methods_compatible(contract.verification_method, raw_method),
            scope_compatible=scopes_compatible(contract.scope, raw_scope),
            parameter_compatible=raw_param_ok,
            is_authoritative=False,
            qualification_status="NOT_QUALIFIED",
            reason="Retrieved chunk does not contain a local passage addressing this requirement.",
        )

    authority = classify_source_authority(
        document_name,
        local_content,
        doc_type,
        document_profile=document_profile,
    )
    passage_modality = classify_passage_modality(local_content, document_profile)
    method = _evidence_method(authority, passage_modality)
    scope = normalize_entity_scope(local_content, document_name)
    params_found = extract_parameters_from_text(local_content)
    if evidence_parameter:
        norm = normalize_parameter(evidence_parameter)
        if norm and norm not in params_found:
            params_found.append(norm)
    primary_param = params_found[0] if params_found else normalize_parameter(evidence_parameter)

    method_ok = methods_compatible(contract.verification_method, method)
    scope_ok = scopes_compatible(contract.scope, scope)
    required_params = _contract_parameters(contract)
    evidence_params = params_found or ([primary_param] if primary_param else [None])
    param_checks = [
        parameters_compatible(param, evidence_param, local_content)
        for param in required_params
        for evidence_param in evidence_params
    ]
    if any(check is True for check in param_checks):
        param_ok = True
    elif param_checks and all(check is False for check in param_checks):
        param_ok = False
    else:
        param_ok = None

    # Authoritative = a formal record OF THE KIND THE REQUIREMENT DEMANDS:
    # empirical / matrix records for physical requirements, a simulation study
    # for simulation requirements, an inspection record for inspection, etc.
    required_method = normalize_required_method(contract.verification_method)
    profile_trusted = not profile_requires_review and (
        profile_confidence is None or float(profile_confidence) >= 65.0
    )
    is_authoritative = profile_trusted and method_ok and authority != "COMPLIANCE_MATRIX" and (
        method == "physical_test" or method == required_method
    )

    # Decision tree — a piece of evidence is only QUALIFIED when it can
    # actually stand in as verification: right method, right entity (or
    # system-level), and nothing contradicts the required parameter.
    reasons: list[str] = []
    if authority == "COMPLIANCE_MATRIX":
        status = "PARTIALLY_QUALIFIED"
        reasons.append("compliance matrix is authoritative for workflow status but references, rather than replaces, technical proof")
    elif not method_ok:
        status = "NOT_QUALIFIED"
        reasons.append(
            f"evidence method '{method}' cannot satisfy required verification "
            f"method '{contract.verification_method or 'physical_test'}'"
        )
    elif scope_ok is False:
        status = "NOT_QUALIFIED"
        reasons.append(
            f"entity scope mismatch: evidence is about '{scope}' while requirement targets '{contract.scope or 'System'}'"
        )
    elif not is_authoritative:
        status = "PARTIALLY_QUALIFIED"
        if not profile_trusted:
            reasons.append("document profile is low-confidence and requires human review")
        else:
            reasons.append(f"authority '{authority}' is method-compatible but not a formal verification record")
    elif scope_ok is None:
        status = "PARTIALLY_QUALIFIED"
        reasons.append("entity scope of the evidence could not be established")
    else:
        status = "QUALIFIED"
        reasons.append(
            f"authoritative '{authority}' record at matching scope with compatible verification method"
        )
    if param_ok is False:
        reasons.append(
            "lexical parameter alignment is uncertain; the condition reasoner must decide semantic relevance"
        )

    return EvidenceQualification(
        evidence_id=evidence_id,
        source_chunk_id=source_chunk_id,
        document_name=document_name,
        document_role=document_role,
        document_profile_confidence=profile_confidence,
        source_authority=authority,
        passage_modality=passage_modality,
        relevance_status=(
            "UNCERTAIN"
            if param_ok is False
            else "RELEVANT"
        ),
        entity_scope=scope,
        parameter=primary_param,
        parameters_found=params_found,
        verification_method=method,
        required_verification_method=contract.verification_method,
        method_compatible=method_ok,
        scope_compatible=scope_ok,
        parameter_compatible=param_ok,
        is_authoritative=is_authoritative,
        qualification_status=status,
        reason="; ".join(reasons).capitalize() + ".",
    )


def qualify_evidence_chunks(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    spec_doc_names: Optional[set[str]] = None,
) -> list[EvidenceQualification]:
    """Qualify all candidate chunks (E1..En in prompt order) for a requirement."""
    quals: list[EvidenceQualification] = []
    for i, chunk in enumerate(evidence_chunks, 1):
        doc_name = chunk.get("document_name") or chunk.get("document_name", "Document")
        if spec_doc_names and doc_name in spec_doc_names:
            continue  # spec self-references are not evidence at all
        quals.append(qualify_evidence(
            contract=contract,
            evidence_id=f"E{i}",
            document_name=doc_name,
            content=(chunk.get("content") or chunk.get("quote") or "").strip(),
            doc_type=chunk.get("doc_type"),
            source_chunk_id=chunk.get("chunk_id") or chunk.get("id"),
            document_profile=chunk.get("document_profile"),
            metadata=chunk.get("metadata"),
        ))
    return quals


def qualify_evidence_claim(
    contract: RequirementContract,
    claim: EvidenceClaim,
) -> EvidenceQualification:
    """Qualify an extracted EvidenceClaim against a contract."""
    return qualify_evidence(
        contract=contract,
        evidence_id=claim.claim_id,
        document_name=claim.document_name,
        content=claim.quote,
        evidence_parameter=claim.parameter,
        source_chunk_id=claim.source_chunk_id,
    )


def condition_evidence_compatible(
    condition: Any,
    qualification: EvidenceQualification,
) -> Optional[bool]:
    """Can this specific condition be proven by this specific (already qualified) evidence?

    Uses per-condition parameter matching: an evidence chunk qualified for the
    requirement may still discuss a different quantity than this condition
    (e.g. contactor latency vs pyro-fuse latency). Returns None when the
    condition's parameter is unrecognized (caller decides, default accept).
    """
    cond_param = normalize_parameter(getattr(condition, "parameter", None))
    if cond_param is None:
        return None
    normalized_found = {
        value
        for value in (normalize_parameter(p) for p in qualification.parameters_found)
        if value
    }
    if cond_param in normalized_found:
        return True
    # Absence from a lexical parameter list is not proof of incompatibility.
    # The semantic reasoner may have mapped a valid cited phrase that this
    # lightweight extractor cannot classify. Requirement-level qualification
    # already rejects explicit parameter conflicts (e.g. contactor latency for
    # a pyro-fuse requirement), so this per-condition check stays tri-state.
    return False if qualification.parameter_compatible is False else None


def format_qualification_annotation(q: EvidenceQualification) -> str:
    """One-line human/LLM-readable annotation for evidence prompts."""
    flag = "✔" if q.qualification_status == "QUALIFIED" else ("~" if q.qualification_status == "PARTIALLY_QUALIFIED" else "✘")
    scope = f", scope={q.entity_scope} (required={q.scope_compatible if q.scope_compatible is not None else 'unknown'})"
    param = f", parameter={q.parameter or 'n/a'}"
    return (
        f"[QUALIFICATION {flag} {q.qualification_status} | role={q.document_role or 'unknown'} "
        f"| authority={q.source_authority} | passage={q.passage_modality} | relevance={q.relevance_status} "
        f"| method={q.verification_method} vs required={q.required_verification_method or 'physical_test'} "
        f"(compatible={str(q.method_compatible).lower()}){scope}{param}] {q.reason}"
    )


def has_qualified_evidence(qualifications: list[EvidenceQualification]) -> bool:
    """True when at least one piece of evidence is fully qualified."""
    return any(q.qualification_status == "QUALIFIED" for q in qualifications)
