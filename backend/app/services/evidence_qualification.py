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

from app.schemas.claim import EvidenceClaim, classify_source_authority
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


def _contract_parameter(contract: RequirementContract) -> Optional[str]:
    """Primary parameter named by the contract (or its first atomic condition)."""
    if contract.parameter:
        return contract.parameter
    for cond in contract.atomic_conditions:
        if cond.parameter:
            return cond.parameter
    return None


def qualify_evidence(
    contract: RequirementContract,
    evidence_id: str,
    document_name: str,
    content: str,
    doc_type: Optional[str] = None,
    evidence_parameter: Optional[str] = None,
) -> EvidenceQualification:
    """Qualify one evidence unit against one requirement contract."""
    authority = classify_source_authority(document_name, content, doc_type)
    method = AUTHORITY_TO_METHOD.get(authority, "unknown")
    scope = normalize_entity_scope(content, document_name)
    params_found = extract_parameters_from_text(content)
    if evidence_parameter:
        norm = normalize_parameter(evidence_parameter)
        if norm and norm not in params_found:
            params_found.append(norm)
    primary_param = params_found[0] if params_found else normalize_parameter(evidence_parameter)

    method_ok = methods_compatible(contract.verification_method, method)
    scope_ok = scopes_compatible(contract.scope, scope)
    param_ok = parameters_compatible(_contract_parameter(contract), primary_param, content)

    # Authoritative = a formal record OF THE KIND THE REQUIREMENT DEMANDS:
    # empirical / matrix records for physical requirements, a simulation study
    # for simulation requirements, an inspection record for inspection, etc.
    required_method = normalize_required_method(contract.verification_method)
    is_authoritative = method_ok and (
        method in ("physical_test", "matrix_record") or method == required_method
    )

    # Decision tree — a piece of evidence is only QUALIFIED when it can
    # actually stand in as verification: right method, right entity (or
    # system-level), and nothing contradicts the required parameter.
    reasons: list[str] = []
    if not method_ok:
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
    elif param_ok is False:
        status = "NOT_QUALIFIED"
        reasons.append(
            f"parameter mismatch: evidence discusses '{primary_param}' while requirement concerns '{_contract_parameter(contract)}'"
        )
    elif not is_authoritative:
        status = "PARTIALLY_QUALIFIED"
        reasons.append(f"authority '{authority}' is method-compatible but not a formal verification record")
    elif scope_ok is None:
        status = "PARTIALLY_QUALIFIED"
        reasons.append("entity scope of the evidence could not be established")
    else:
        status = "QUALIFIED"
        reasons.append(
            f"authoritative '{authority}' record at matching scope with compatible verification method"
        )

    return EvidenceQualification(
        evidence_id=evidence_id,
        document_name=document_name,
        source_authority=authority,
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
    if cond_param in qualification.parameters_found:
        return True
    if qualification.parameters_found:
        # Evidence names specific quantities, none of which is ours.
        return False
    return None


def format_qualification_annotation(q: EvidenceQualification) -> str:
    """One-line human/LLM-readable annotation for evidence prompts."""
    flag = "✔" if q.qualification_status == "QUALIFIED" else ("~" if q.qualification_status == "PARTIALLY_QUALIFIED" else "✘")
    scope = f", scope={q.entity_scope} (required={q.scope_compatible if q.scope_compatible is not None else 'unknown'})"
    param = f", parameter={q.parameter or 'n/a'}"
    return (
        f"[QUALIFICATION {flag} {q.qualification_status} | authority={q.source_authority} "
        f"| method={q.verification_method} vs required={q.required_verification_method or 'physical_test'} "
        f"(compatible={str(q.method_compatible).lower()}){scope}{param}] {q.reason}"
    )


def has_qualified_evidence(qualifications: list[EvidenceQualification]) -> bool:
    """True when at least one piece of evidence is fully qualified."""
    return any(q.qualification_status == "QUALIFIED" for q in qualifications)
