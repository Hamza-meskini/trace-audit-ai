"""Coverage classification service.

Combines structured requirement contracts, factual evidence claims,
modular deterministic validators, and contradiction detection into a
conservative, auditable requirement coverage decision engine.

All entry points (sync, async, batched) share the same deterministic
pre-check chain and finalization logic. The async and batched variants
additionally escalate inconclusive (UNKNOWN) cases to the LLM
verification reasoner.
"""

import asyncio
import time
import re
from typing import Optional, Any
from dataclasses import dataclass, field

from app.schemas.contract import RequirementContract, parse_requirement_contract
from app.schemas.claim import extract_all_evidence_claims
from app.services.contradiction import detect_cross_document_contradiction, ContradictionFinding
from app.services.validators.numeric_range import validate_numeric_range
from app.services.validators.threshold import validate_threshold
from app.services.validators.duration import validate_duration
from app.services.validators.boolean_flag import validate_boolean_flag
from app.services.validators.test_verdict import validate_test_verdict
from app.services.validators.semantic import validate_semantic
from app.services.validators import ValidationOutcome
from app.services.verification_reasoner import SPEC_DOC_KEYWORDS
from app.schemas.verification_result import ConditionVerificationResult


@dataclass
class EvidenceLinkAssessment:
    chunk_id: str
    document_name: str
    page_number: Optional[int]
    quote: str
    status: str  # "Supports requirement" | "Potential conflict" | "Supporting evidence"
    label: str
    highlight: Optional[str] = None


@dataclass
class RequirementAssessment:
    coverage_status: str  # "Supported" | "Partial" | "Missing" | "Conflict" | "Unknown"
    confidence: float
    review_state: str     # "Reviewed" | "Needs review" | "Open"
    ai_analysis: str
    ai_recommendation: str
    evidence_links: list[EvidenceLinkAssessment] = field(default_factory=list)
    contract: Optional[RequirementContract] = None
    condition_results: list[ConditionVerificationResult] = field(default_factory=list)
    pipeline_diagnostics: dict[str, Any] = field(default_factory=dict)


RECOMMENDATIONS = {
    "Supported": "No immediate action required. Retain the current evidence set for the technical compliance file.",
    "Partial": "Extend qualification testing to cover remaining parameter bounds or attach completed test records.",
    "Conflict": "Review contradictory technical documentation with engineering stakeholders.",
    "Missing": "Upload the relevant test plan, test report, or compliance record covering this requirement.",
    "Unknown": "Evidence is inconclusive (e.g. simulation, calculation, or design intent only). Request empirical test records or an authoritative verification record.",
}


SUPPORTED_AUTO_CLOSE_MIN_CONFIDENCE = 90.0


def _supporting_results_for_review(
    contract: Optional[RequirementContract],
    condition_results: list[ConditionVerificationResult],
) -> tuple[list[ConditionVerificationResult], list[str]]:
    """Return the proof path and unresolved IDs relevant to a SUPPORTED verdict."""
    if contract is None:
        unresolved = [
            result.condition_id for result in condition_results
            if result.status not in ("PROVEN", "NOT_APPLICABLE")
        ]
        return condition_results, unresolved

    by_id = {result.condition_id: result for result in condition_results}
    logic = contract.logic
    if logic.operator == "ANY_OF":
        governed = [by_id[item] for item in logic.condition_ids if item in by_id]
        proven = [result for result in governed if result.status == "PROVEN"]
        return proven, [] if proven else list(logic.condition_ids)

    if logic.operator == "IF_THEN":
        antecedent = by_id.get(logic.if_condition_id or "")
        if antecedent is not None and antecedent.status == "NOT_APPLICABLE":
            return [antecedent], []
        consequents = [by_id[item] for item in logic.then_condition_ids if item in by_id]
        unresolved = [
            item for item in logic.then_condition_ids
            if item not in by_id or by_id[item].status != "PROVEN"
        ]
        proof = ([antecedent] if antecedent is not None else []) + consequents
        if antecedent is None or antecedent.status != "PROVEN":
            unresolved.insert(0, logic.if_condition_id or "antecedent")
        return proof, unresolved

    governed_ids = list(logic.condition_ids) or [
        condition.condition_id for condition in contract.verification_conditions
        if condition.mandatory
    ]
    governed = [by_id[item] for item in governed_ids if item in by_id]
    unresolved = [
        item for item in governed_ids
        if item not in by_id or by_id[item].status not in ("PROVEN", "NOT_APPLICABLE")
    ]
    return governed, unresolved


def _supported_review_gate(
    outcome: ValidationOutcome,
    pipeline_diagnostics: Optional[dict[str, Any]] = None,
    contract: Optional[RequirementContract] = None,
) -> list[str]:
    """Return reasons why a SUPPORTED prediction must remain under review.

    This is an operational safety gate, not a semantic verdict override.  The
    LLM-owned condition statuses and their mechanically aggregated coverage
    category remain unchanged.  The gate only decides whether the business
    workflow may automatically close a supported requirement.
    """
    if outcome.status != "SUPPORTED":
        return []

    reasons: list[str] = []
    condition_results = list(outcome.condition_results or [])
    diagnostics = pipeline_diagnostics or {}

    if contract is not None and contract.contract_complete is False:
        if contract.unmapped_obligations:
            preview = "; ".join(contract.unmapped_obligations[:3])
            if len(contract.unmapped_obligations) > 3:
                preview += f"; +{len(contract.unmapped_obligations) - 3} more"
            reasons.append(
                "Atomic-condition extraction is incomplete; unmapped obligation(s): "
                + preview
                + "."
            )
        elif not contract.clause_coverage:
            reasons.append(
                "Atomic-condition extraction completeness was not established: "
                "no clause-to-condition coverage map was supplied."
            )
        else:
            reasons.append(
                "Atomic-condition extraction reported an incomplete or internally "
                "inconsistent clause-to-condition coverage map."
            )

    provisional_status = str(diagnostics.get("llm_provisional_status") or "").strip().upper()
    if provisional_status and provisional_status != "SUPPORTED":
        reasons.append(
            "The LLM's holistic provisional verdict "
            f"({provisional_status}) disagrees with condition aggregation (SUPPORTED)."
        )

    if not condition_results:
        reasons.append("No auditable atomic-condition results were supplied.")
    else:
        proof_results, non_proven = _supporting_results_for_review(contract, condition_results)
        contradicted = [
            result.condition_id
            for result in proof_results
            if result.status == "PROVEN" and result.validation_state == "CONTRADICTED"
        ]
        if contradicted:
            reasons.append(
                "Evidence audit contradicted the attributed proof for: "
                + ", ".join(contradicted)
                + "."
            )

        unresolved = [
            result.condition_id
            for result in proof_results
            if result.status == "PROVEN" and result.validation_state != "VALID"
            and result.condition_id not in contradicted
        ]
        if unresolved:
            reasons.append(
                "Evidence admissibility remains unresolved for: "
                + ", ".join(unresolved)
                + "."
            )

    confidence_source = "aggregated"
    review_confidence = outcome.confidence
    raw_llm_confidence = diagnostics.get("llm_provisional_confidence")
    if raw_llm_confidence is not None:
        try:
            review_confidence = float(raw_llm_confidence)
            confidence_source = "LLM"
        except (TypeError, ValueError):
            pass

    if review_confidence < SUPPORTED_AUTO_CLOSE_MIN_CONFIDENCE:
        reasons.append(
            f"{confidence_source} confidence {review_confidence:.1f}% is below the "
            f"{SUPPORTED_AUTO_CLOSE_MIN_CONFIDENCE:.0f}% automatic-closure threshold."
        )

    if condition_results and non_proven:
        reasons.append(
            "Not every applicable atomic condition is PROVEN: "
            + ", ".join(non_proven)
            + "."
        )

    return reasons


def _condition_results_for_status(
    contract: RequirementContract,
    status: str,
) -> list[ConditionVerificationResult]:
    """Create honest atomic placeholders when only a top-level record exists.

    A requirement-level PASS/FAIL/PARTIAL record does not establish that every
    atomic condition has the same state. Only an explicit absence record can
    safely map every condition to UNTESTED; all other undifferentiated
    top-level outcomes remain INCONCLUSIVE until condition evidence is read.
    """
    top_level_status = status.upper()
    condition_status = "UNTESTED" if top_level_status == "MISSING" else "INCONCLUSIVE"
    reason = (
        "Authoritative workflow record confirms that verification evidence is absent."
        if condition_status == "UNTESTED"
        else (
            f"Top-level {top_level_status} record does not identify the outcome of this "
            "individual atomic condition."
        )
    )
    conditions = contract.atomic_conditions or []
    return [
        ConditionVerificationResult(
            condition_id=c.condition_id,
            description=c.description,
            status=condition_status,
            reason=reason,
        )
        for c in conditions
    ]


def _format_evidence_items(candidate_chunks: list[dict]) -> list[dict]:
    """Normalize candidate chunks into the evidence-item dict used by all checks."""
    return [
        {
            "chunk_id": c.get("id") or c.get("chunk_id", ""),
            "document_name": c.get("document_name", "Document"),
            "doc_type": c.get("doc_type", "Document"),
            "page_number": c.get("page_number"),
            "quote": c.get("content") or c.get("quote", ""),
            "document_profile": c.get("document_profile"),
        }
        for c in candidate_chunks
    ]


def _filter_non_spec(
    evidence_items: list[dict],
    spec_doc_names: Optional[set[str]] = None,
) -> list[dict]:
    """Drop self-referential specification chunks (SRS / product requirements docs / design constraints)."""
    if spec_doc_names:
        return [
            e for e in evidence_items
            if e.get("document_name") not in spec_doc_names
            and not any(k in e.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS)
        ]
    return [
        e for e in evidence_items
        if not any(k in e.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS)
    ]


def _missing_assessment(contract: RequirementContract, empty_index: bool = False) -> RequirementAssessment:
    if empty_index:
        analysis = "No evidence segment in the indexed document set addresses this requirement."
    else:
        analysis = "No independent test report, component datasheet, or compliance matrix was found verifying this requirement."
    return RequirementAssessment(
        coverage_status="Missing",
        confidence=95.0,
        review_state="Open",
        ai_analysis=analysis,
        ai_recommendation="Upload the relevant test plan, test report, or compliance record covering this requirement.",
        evidence_links=[],
        contract=contract,
        condition_results=_condition_results_for_status(contract, "MISSING"),
        pipeline_diagnostics={"decision_source": "deterministic_precheck", "final_status": "MISSING"},
    )


def _conflict_assessment(
    contract: RequirementContract,
    evidence_items: list[dict],
    contradiction: ContradictionFinding,
) -> RequirementAssessment:
    links = []
    for e in evidence_items:
        is_conflict_source = contradiction.highlight and contradiction.highlight.lower() in e["quote"].lower()
        status = "Potential conflict" if is_conflict_source else "Supports requirement"
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"].replace(".pdf", "").replace(".docx", "")
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status=status,
            label=label,
            highlight=contradiction.highlight if is_conflict_source else None,
        ))

    return RequirementAssessment(
        coverage_status="Conflict",
        confidence=92.0,
        review_state="Needs review",
        ai_analysis=contradiction.description,
        ai_recommendation="Review the conflicting documentation with engineering and confirm the verified operating bounds before sign-off.",
        evidence_links=links,
        contract=contract,
        condition_results=_condition_results_for_status(contract, "CONFLICT"),
        pipeline_diagnostics={"decision_source": "deterministic_contradiction", "final_status": "CONFLICT"},
    )


def _partial_condition_results_from_claims(
    contract: RequirementContract,
    claims: list[Any],
) -> list[ConditionVerificationResult]:
    """Resolve condition detail carried by an authoritative in-progress record."""
    pending_words = ("pending", "in progress", "not started", "remaining", "deferred")
    proven_words = ("validated", "verified", "completed", "passed", " pass")
    clauses = [
        clause.strip()
        for claim in claims
        if getattr(claim, "test_result", None) in ("IN PROGRESS", "PARTIAL")
        for clause in re.split(r"[;,]", getattr(claim, "quote", ""))
        if clause.strip()
    ]
    results: list[ConditionVerificationResult] = []
    for condition in contract.atomic_conditions:
        condition_tokens = {
            token
            for token in re.findall(
                r"[a-z0-9]+",
                f"{condition.parameter or ''} {condition.description or ''} {condition.threshold or ''}".lower(),
            )
            if len(token) > 2 or any(ch.isdigit() for ch in token)
        }
        best_clause = max(
            clauses,
            key=lambda clause: len(condition_tokens & set(re.findall(r"[a-z0-9]+", clause.lower()))),
            default="",
        )
        best_lower = best_clause.lower()
        if best_clause and any(word in best_lower for word in pending_words):
            status = "PENDING"
        elif best_clause and any(word in best_lower for word in proven_words):
            status = "PROVEN"
        else:
            status = "PENDING"
        results.append(ConditionVerificationResult(
            condition_id=condition.condition_id,
            description=condition.description,
            status=status,
            quote=best_clause or None,
            reason=(
                "Condition state reported by the authoritative in-progress verification record."
                if best_clause
                else "Verification is in progress; no completed condition-specific result was reported."
            ),
        ))
    return results


def _verdict_assessment(
    contract: RequirementContract,
    verdict_outcome: ValidationOutcome,
    claims: Optional[list[Any]] = None,
) -> RequirementAssessment:
    status_map = {
        "MISSING": "Missing",
        "PARTIAL": "Partial",
        "CONFLICT": "Conflict",
    }
    cov_status = status_map.get(verdict_outcome.status, "Partial")
    rev_state = "Open" if cov_status == "Missing" else "Needs review"
    condition_results = verdict_outcome.condition_results
    if not condition_results and verdict_outcome.status == "PARTIAL":
        condition_results = _partial_condition_results_from_claims(contract, claims or [])
    return RequirementAssessment(
        coverage_status=cov_status,
        confidence=verdict_outcome.confidence,
        review_state=rev_state,
        ai_analysis=verdict_outcome.reason,
        ai_recommendation="Schedule testing or review in-progress validation records." if cov_status != "Conflict" else "Investigate test failure root cause.",
        evidence_links=[],
        contract=contract,
        condition_results=(
            condition_results
            or _condition_results_for_status(contract, verdict_outcome.status)
        ),
        pipeline_diagnostics={
            "decision_source": "deterministic_workflow_record",
            "final_status": verdict_outcome.status,
        },
    )


def _run_deterministic_validators(contract: RequirementContract, claims: list) -> Optional[ValidationOutcome]:
    """Run type-specific deterministic validators, falling back to semantic validation."""
    has_empirical = any(
        getattr(c, "source_authority", None) in ("EMPIRICAL_TEST", "QUALIFICATION_TEST", "VALIDATION_REPORT")
        for c in claims
    )
    is_physical = contract.verification_method != "simulation" and contract.verification_method != "calculation"

    outcome: Optional[ValidationOutcome] = None

    if contract.requirement_type == "numeric_range":
        outcome = validate_numeric_range(contract, claims)
    elif contract.requirement_type == "duration":
        outcome = validate_duration(contract, claims)
    elif contract.requirement_type == "threshold":
        outcome = validate_threshold(contract, claims)
    elif contract.requirement_type in ("boolean", "enumeration"):
        outcome = validate_boolean_flag(contract, claims)

    if outcome is None and has_empirical:
        outcome = validate_semantic(contract, claims)

    # If an outcome is SUPPORTED but physical testing is required without empirical evidence -> UNKNOWN
    if outcome and outcome.status == "SUPPORTED" and is_physical and not has_empirical:
        return ValidationOutcome(
            status="UNKNOWN",
            confidence=85.0,
            reason="Evidence consists only of theoretical simulations, calculations, or architecture specifications without empirical test data.",
        )

    return outcome



def _deterministic_prechecks(
    contract: RequirementContract,
    candidate_chunks: list[dict],
    spec_doc_names: Optional[set[str]] = None,
    defer_missing: bool = False,
    defer_partial: bool = False,
    defer_conflict: bool = False,
) -> tuple[Optional[tuple[list[dict], list[dict], list]], Optional[RequirementAssessment]]:
    """Shared deterministic pre-check chain: empty evidence, spec self-reference,
    cross-document contradictions, and compliance-matrix verdicts.

    Returns (None, assessment) when a conclusive decision is reached, otherwise
    ((evidence_items, non_spec_items, claims), None) for downstream validators.
    """
    if not candidate_chunks:
        return None, _missing_assessment(contract, empty_index=True)

    evidence_items = _format_evidence_items(candidate_chunks)
    non_spec_items = _filter_non_spec(evidence_items, spec_doc_names=spec_doc_names)

    # If only specification self-chunks were retrieved, no independent test record exists
    if not non_spec_items:
        return None, _missing_assessment(contract)

    claims = extract_all_evidence_claims(evidence_items, contract)

    # Formal compliance matrix test verdicts (e.g. NOT STARTED, IN PROGRESS, PASS, FAIL)
    verdict_outcome = validate_test_verdict(contract, claims)
    if verdict_outcome and verdict_outcome.status == "MISSING" and not defer_missing:
        return None, _verdict_assessment(contract, verdict_outcome, claims=claims)
    if verdict_outcome and verdict_outcome.status == "CONFLICT" and not defer_conflict:
        return None, _verdict_assessment(contract, verdict_outcome, claims=claims)
    if verdict_outcome and verdict_outcome.status == "PARTIAL" and not defer_partial:
        return None, _verdict_assessment(contract, verdict_outcome, claims=claims)

    # Cross-document / contract contradictions -> CONFLICT
    contradiction: Optional[ContradictionFinding] = detect_cross_document_contradiction(evidence_items, contract)
    if contradiction and contradiction.has_conflict and not defer_conflict:
        return None, _conflict_assessment(contract, evidence_items, contradiction)

    return (evidence_items, non_spec_items, claims), None


def _finalize_assessment(
    contract: RequirementContract,
    non_spec_items: list[dict],
    outcome: Optional[ValidationOutcome],
    pipeline_diagnostics: Optional[dict[str, Any]] = None,
) -> RequirementAssessment:
    """Map a validation outcome into a RequirementAssessment with evidence links."""
    status_mapping = {
        "SUPPORTED": ("Supported", "Reviewed"),
        "PARTIAL": ("Partial", "Needs review"),
        "CONFLICT": ("Conflict", "Needs review"),
        "MISSING": ("Missing", "Open"),
        # Inconclusive evidence (simulation / design intent / ambiguous) is surfaced
        # as a first-class "Unknown" status instead of being silently downgraded.
        "UNKNOWN": ("Unknown", "Needs review"),
    }

    if outcome is None:
        outcome = ValidationOutcome(
            status="UNKNOWN",
            confidence=70.0,
            reason="Evidence could not be deterministically verified.",
        )

    diagnostics = dict(pipeline_diagnostics or {
        "decision_source": "deterministic_validator",
        "final_status": outcome.status,
    })
    cov_status, rev_state = status_mapping.get(outcome.status, ("Unknown", "Needs review"))
    review_reasons = _supported_review_gate(outcome, diagnostics, contract)
    if cov_status == "Supported" and review_reasons:
        rev_state = "Needs review"

    diagnostics["review_gate"] = {
        "policy": "supported_evidence_and_contract_audit_v2",
        "required": bool(review_reasons),
        "auto_close_eligible": cov_status == "Supported" and not review_reasons,
        "reasons": review_reasons,
    }

    highlight = outcome.highlight
    links = []
    for e in non_spec_items:
        label = e["doc_type"] if e["doc_type"] != "Document" else e["document_name"]
        is_highlight_src = highlight and highlight.lower() in e["quote"].lower()
        link_status = "Potential conflict" if is_highlight_src else ("Supports requirement" if cov_status == "Supported" else "Supporting evidence")
        links.append(EvidenceLinkAssessment(
            chunk_id=e["chunk_id"],
            document_name=e["document_name"],
            page_number=e["page_number"],
            quote=e["quote"],
            status=link_status,
            label=label,
            highlight=highlight if is_highlight_src else None,
        ))

    recommendation = RECOMMENDATIONS.get(cov_status, "Perform engineering review.")
    if cov_status == "Supported" and review_reasons:
        recommendation = (
            "Review the atomic proof, extraction completeness, and evidence-admissibility "
            "warnings before approval. "
            "The predicted coverage remains Supported, but automatic closure is disabled."
        )

    return RequirementAssessment(
        coverage_status=cov_status,
        confidence=outcome.confidence,
        review_state=rev_state,
        ai_analysis=outcome.reason,
        ai_recommendation=recommendation,
        evidence_links=links,
        contract=contract,
        condition_results=(outcome.condition_results or _condition_results_for_status(contract, outcome.status)),
        pipeline_diagnostics=diagnostics,
    )


def assess_requirement_coverage(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
    spec_doc_names: Optional[set[str]] = None,
    conditions: Optional[list[dict[str, Any]]] = None,
    clause_coverage: Optional[list[dict[str, Any]]] = None,
    unmapped_obligations: Optional[list[str]] = None,
    contract_complete: Optional[bool] = None,
    logic: Optional[dict[str, Any]] = None,
) -> RequirementAssessment:
    """Assess a requirement using the deterministic validation engine only (no LLM calls)."""
    contract = parse_requirement_contract(
        req_code=req_code,
        title=title,
        description=description,
        category=category,
        structured_conditions=conditions,
        clause_coverage=clause_coverage,
        unmapped_obligations=unmapped_obligations,
        contract_complete=contract_complete,
        logic=logic,
    )

    context, decided = _deterministic_prechecks(
        contract,
        candidate_chunks,
        spec_doc_names=spec_doc_names,
    )
    if decided:
        return decided

    _evidence_items, non_spec_items, _claims = context

    # One authoritative decision path for both single- and multi-condition
    # requirements. Type-specific validators remain available as helpers, but
    # they cannot bypass evidence qualification and condition aggregation.
    from app.services.verification_reasoner import rule_based_multi_condition_verification
    reasoner_result = rule_based_multi_condition_verification(
        contract, candidate_chunks, spec_doc_names=spec_doc_names
    )
    validation_outcome = ValidationOutcome(
        status=reasoner_result.status,
        confidence=float(reasoner_result.confidence),
        reason=reasoner_result.reason,
        highlight=reasoner_result.highlight,
        condition_results=reasoner_result.condition_results,
    )

    return _finalize_assessment(
        contract,
        non_spec_items,
        validation_outcome,
        pipeline_diagnostics=dict(getattr(reasoner_result, "_diagnostics", {})),
    )


async def assess_requirement_coverage_async(
    req_code: str,
    title: str,
    description: Optional[str],
    category: str,
    candidate_chunks: list[dict],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    spec_doc_names: Optional[set[str]] = None,
    conditions: Optional[list[dict[str, Any]]] = None,
    clause_coverage: Optional[list[dict[str, Any]]] = None,
    unmapped_obligations: Optional[list[str]] = None,
    contract_complete: Optional[bool] = None,
    logic: Optional[dict[str, Any]] = None,
) -> RequirementAssessment:
    """Async assessment that escalates inconclusive cases to the LLM verification reasoner."""
    contract = parse_requirement_contract(
        req_code=req_code,
        title=title,
        description=description,
        category=category,
        structured_conditions=conditions,
        clause_coverage=clause_coverage,
        unmapped_obligations=unmapped_obligations,
        contract_complete=contract_complete,
        logic=logic,
    )

    context, decided = _deterministic_prechecks(
        contract,
        candidate_chunks,
        spec_doc_names=spec_doc_names,
        defer_missing=True,
        defer_conflict=True,
    )
    if decided:
        return decided

    _evidence_items, non_spec_items, _claims = context

    # Single- and multi-condition requirements share the same semantic
    # reasoner and finalizer. This prevents a keyword validator from approving
    # evidence before qualification has run.
    from app.services.verification_reasoner import evaluate_requirement_verification
    reasoner_result = await evaluate_requirement_verification(
        contract=contract,
        evidence_chunks=candidate_chunks,
        model=model,
        thinking_level=thinking_level,
        spec_doc_names=spec_doc_names,
    )
    validation_outcome = ValidationOutcome(
        status=reasoner_result.status,
        confidence=float(reasoner_result.confidence),
        reason=reasoner_result.reason,
        highlight=reasoner_result.highlight,
        condition_results=reasoner_result.condition_results,
    )

    return _finalize_assessment(
        contract,
        non_spec_items,
        validation_outcome,
        pipeline_diagnostics=dict(getattr(reasoner_result, "_diagnostics", {})),
    )


async def batch_assess_requirements(
    req_items: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    batch_size: int = 3,
    spec_doc_names: Optional[set[str]] = None,
) -> dict[str, RequirementAssessment]:
    """Assess a batch of requirements: deterministic checks first, batched LLM reasoning for the rest.

    Batches inconclusive requirements in groups of `batch_size` to limit rate-limit
    pressure while maintaining full multi-condition semantic reasoning.
    """
    from app.services.verification_reasoner import evaluate_batch_verification

    assessments: dict[str, RequirementAssessment] = {}
    pre_processed = []

    # Step 1: deterministic pre-checks and type-specific validators per requirement
    for item in req_items:
        req_code = item["req_code"]
        contract = parse_requirement_contract(
            req_code=req_code,
            title=item.get("title", ""),
            description=item.get("description", ""),
            category=item.get("category", "General"),
            structured_conditions=item.get("conditions"),
            clause_coverage=item.get("clause_coverage"),
            unmapped_obligations=item.get("unmapped_obligations"),
            contract_complete=item.get("contract_complete"),
            logic=item.get("logic"),
        )
        candidate_chunks = item.get("candidate_chunks", [])

        context, decided = _deterministic_prechecks(
            contract,
            candidate_chunks,
            spec_doc_names=spec_doc_names,
            defer_missing=True,
            defer_partial=True,
            defer_conflict=True,
        )
        if decided:
            assessments[req_code] = decided
            continue

        _evidence_items, non_spec_items, _claims = context

        # Every unresolved requirement is queued for the same qualified
        # condition-level reasoner, regardless of condition count.
        pre_processed.append({
            "req_code": req_code,
            "contract": contract,
            "candidate_chunks": candidate_chunks,
            "non_spec_items": non_spec_items,
            "spec_doc_names": spec_doc_names,
        })


    # Step 2: process queued requirements in batches
    import time
    total_batches = (len(pre_processed) + batch_size - 1) // max(batch_size, 1)
    print(f"  • Evaluating {len(pre_processed)} queued requirements across {total_batches} batches (batch_size={batch_size})...", flush=True)

    for b_idx, i in enumerate(range(0, len(pre_processed), batch_size), 1):
        batch = pre_processed[i : i + batch_size]
        t0 = time.time()
        batch_codes = [it["req_code"] for it in batch]
        batch_results = await evaluate_batch_verification(
            batch_items=batch,
            model=model,
            thinking_level=thinking_level,
        )
        dt = time.time() - t0

        for item in batch:
            req_code = item["req_code"]
            res = batch_results.get(req_code)
            if res:
                outcome = ValidationOutcome(
                    status=res.status,
                    confidence=float(res.confidence),
                    reason=res.reason,
                    highlight=res.highlight,
                    condition_results=res.condition_results,
                )
            else:
                outcome = ValidationOutcome(
                    status="UNKNOWN",
                    confidence=75.0,
                    reason="Evaluated through compliance assessment engine.",
                )
            pipeline_diagnostics = dict(getattr(res, "_diagnostics", {}) if res else {})
            assessments[req_code] = _finalize_assessment(
                item["contract"],
                item["non_spec_items"],
                outcome,
                pipeline_diagnostics=pipeline_diagnostics,
            )

        done_count = min(i + len(batch), len(pre_processed))
        first_code = batch_codes[0] if batch_codes else "?"
        last_code = batch_codes[-1] if batch_codes else "?"
        print(f"    -> [Batch {b_idx:02d}/{total_batches:02d}] {first_code}..{last_code} ({len(batch)} reqs) in {dt:.2f}s | Done: {done_count}/{len(pre_processed)} ({done_count/len(pre_processed)*100:.0f}%)", flush=True)

        if i + batch_size < len(pre_processed):
            await asyncio.sleep(0.5)

    return assessments
