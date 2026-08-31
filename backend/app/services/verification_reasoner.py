"""Structured multi-condition LLM reasoner with non-mutating audit diagnostics."""

import re
import logging
from typing import Optional, Any

from app.config import settings
from app.schemas.contract import RequirementContract, AtomicConditionContract
from app.schemas.claim import (
    extract_all_evidence_claims,
    SourceAuthority,
)
from app.schemas.verification_result import (
    ConditionVerificationResult,
    VerificationAnalysisResult,
    BatchVerificationResult,
)
from app.services.llm_client import generate_structured
from app.services.units import (
    UnitCompatibility,
    convert_value,
    unit_compatibility,
)
from app.services.evidence_qualification import (
    qualify_evidence_chunks,
    format_qualification_annotation,
)
from app.schemas.claim import _isolate_relevant_passage
from app.services.verdict_aggregator import (
    aggregate_condition_statuses,
    finalize_verdict,
    condition_results_from_claims,
)

logger = logging.getLogger("traceaudit.verifier")


def _structured_numeric_status(
    condition: AtomicConditionContract,
    result: ConditionVerificationResult,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Validate numeric facts emitted in structured LLM fields.

    Unlike raw-text regex extraction, this comparison operates on the model's
    explicit observed value and role. It therefore cannot confuse a nearby
    requirement target with a measurement.
    """
    if result.evidence_value_role == "REQUIRED_OR_PLANNED":
        return None, "The cited numeric value is marked required/planned, not observed.", "UNRESOLVED"
    if result.evidence_value_role not in ("OBSERVED", "UNCLEAR"):
        return None, None, None
    if not condition.unit or not result.observed_unit:
        return None, None, None
    compatibility = unit_compatibility(result.observed_unit, condition.unit)
    if compatibility == UnitCompatibility.UNKNOWN:
        return None, (
            f"Unit parser could not safely resolve observed unit '{result.observed_unit}' "
            f"against required unit '{condition.unit}'; semantic status was preserved."
        ), "UNRESOLVED"
    if compatibility == UnitCompatibility.INCOMPATIBLE:
        return None, (
            f"Observed unit '{result.observed_unit}' is dimensionally incompatible with "
            f"required unit '{condition.unit}'."
        ), "CONTRADICTED"

    op = (condition.operator or "").strip()
    threshold = condition.threshold
    numeric_threshold = (
        float(threshold)
        if isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
        else None
    )

    observed_value: Optional[float] = None
    observed_value_was_numeric = False
    if result.observed_value is not None:
        try:
            observed_value = float(str(result.observed_value).replace(",", "").strip())
            observed_value_was_numeric = True
        except ValueError:
            observed_value = None
    if observed_value is not None:
        observed_value = convert_value(observed_value, result.observed_unit, condition.unit)
        if observed_value is None and observed_value_was_numeric:
            return None, (
                "Recognized units could not be converted safely; semantic status was preserved."
            ), "UNRESOLVED"

    if numeric_threshold is not None and observed_value is not None:
        if op in ("<=", "<"):
            return ("PROVEN" if observed_value <= numeric_threshold else "FAILED"), None, None
        if op in (">=", ">"):
            return ("PROVEN" if observed_value >= numeric_threshold else "FAILED"), None, None
        if op in ("==", "="):
            tolerance = max(abs(numeric_threshold) * 0.025, 1e-9)
            return ("PROVEN" if abs(observed_value - numeric_threshold) <= tolerance else "FAILED"), None, None

    if (
        condition.min_value is not None
        and condition.max_value is not None
        and result.observed_min_value is not None
        and result.observed_max_value is not None
    ):
        observed_min = convert_value(result.observed_min_value, result.observed_unit, condition.unit)
        observed_max = convert_value(result.observed_max_value, result.observed_unit, condition.unit)
        if observed_min is None or observed_max is None:
            return None, (
                "Recognized units could not be converted safely; semantic status was preserved."
            ), "UNRESOLVED"
        if observed_min <= condition.min_value and observed_max >= condition.max_value:
            return "PROVEN", None, None
        if observed_min <= condition.max_value and observed_max >= condition.min_value:
            return "PENDING", None, None

    return None, None, None


def _snapshot_condition_results(
    condition_results: list[ConditionVerificationResult],
) -> list[dict[str, Any]]:
    return [result.model_dump(exclude_none=True) for result in condition_results]


def _record_reconciliation_diagnostics(
    analysis: VerificationAnalysisResult,
    original: list[dict[str, Any]],
) -> None:
    reconciled = _snapshot_condition_results(analysis.condition_results)
    after_by_id = {item.get("condition_id"): item for item in reconciled}
    transitions = []
    for previous in original:
        current = after_by_id.get(previous.get("condition_id"))
        if current is None or previous.get("status") == current.get("status"):
            continue
        transitions.append({
            "stage": "structured_validation",
            "condition_id": previous.get("condition_id"),
            "from_status": previous.get("status"),
            "to_status": current.get("status"),
            "reason": current.get("reason"),
        })
    analysis._diagnostics.update({
        "llm_condition_results": original,
        "post_reconciliation_condition_results": reconciled,
        "condition_transitions": transitions,
    })


def _audit_llm_condition_metadata(
    contract: RequirementContract,
    analysis: VerificationAnalysisResult,
) -> VerificationAnalysisResult:
    """Audit structured model facts without changing semantic condition statuses.

    The LLM owns PROVEN/FAILED/PENDING/UNTESTED/INCONCLUSIVE.  Python records
    inconsistencies as validation metadata so they remain visible to reviewers,
    but those warnings are not a second semantic prediction engine.
    """
    conditions = {condition.condition_id: condition for condition in contract.atomic_conditions}

    for result in analysis.condition_results:
        result.validation_state = "UNRESOLVED"
        result.validation_notes = []
        condition = conditions.get(result.condition_id)
        if condition is None:
            result.validation_notes.append(
                "No matching atomic contract was available; semantic result was preserved."
            )
            continue

        is_numeric = (
            condition.operator in ("<=", "<", ">=", ">", "==", "=", "between")
            and (
                (
                    isinstance(condition.threshold, (int, float))
                    and not isinstance(condition.threshold, bool)
                )
                or condition.min_value is not None
                or condition.max_value is not None
            )
        )
        structured_status, structured_problem, problem_state = (
            _structured_numeric_status(condition, result)
            if is_numeric else (None, None, None)
        )
        decisive = result.status in ("PROVEN", "FAILED")
        if structured_problem:
            result.validation_state = (
                "CONTRADICTED"
                if problem_state == "CONTRADICTED" and decisive
                else "UNRESOLVED"
            )
            result.validation_notes.append(structured_problem)
        elif (
            structured_status
            and decisive
            and result.status != structured_status
        ):
            result.validation_state = "CONTRADICTED"
            result.validation_notes.append(
                f"Structured observed values do not support semantic status {result.status}; "
                "the LLM status was preserved."
            )
        elif structured_status == result.status:
            result.validation_state = "VALID"
            result.validation_notes.append("Structured observed values confirm the semantic status.")
        elif (
            is_numeric
            and result.status in ("PROVEN", "FAILED", "PENDING")
            and result.validation_state not in ("VALID", "CONTRADICTED")
        ):
            result.validation_notes.append(
                "Structured numeric fields were insufficient for an independent consistency check; "
                "semantic status was preserved."
            )

        # A self-inconsistent PENDING record is an audit warning, not a status
        # rewrite. The model's condition label remains the aggregation input.
        if result.status == "PENDING" and (
            result.relationship == "NOT_ADDRESSED"
            or result.evidence_value_role == "NOT_ADDRESSED"
        ):
            result.validation_state = "CONTRADICTED"
            result.validation_notes.append(
                "PENDING conflicts with structured NOT_ADDRESSED metadata; the LLM status was preserved."
            )

        expected_relationship = {
            "PROVEN": "SATISFIES",
            "FAILED": "VIOLATES",
            "PENDING": "PARTIAL_COVERAGE",
        }.get(result.status)
        if expected_relationship and result.relationship not in ("UNCLEAR", expected_relationship):
            if result.validation_state != "CONTRADICTED":
                result.validation_state = "UNRESOLVED"
            result.validation_notes.append(
                f"Structured relationship {result.relationship} is inconsistent with status {result.status}; "
                "semantic status was preserved."
            )

    return analysis

# Filenames that identify specification documents (self-referential, not independent evidence)
SPEC_DOC_KEYWORDS = (
    "srs",
    "product_requirements",
    "requirements_specification",
    "requirement_spec",
    "system_requirements",
    "technical_spec",
    "design_constraints",
    "design_constraint",
    "system_definition",
    "functional_spec",
    "prd",
    "prs",
)

# Document-name markers for formal verification tracking records
COMPLIANCE_MATRIX_KEYWORDS = ("matrix", "verification_matrix", "compliance_matrix")

# Generic incomplete-verification wording found in test reports and matrices
PARTIAL_EVIDENCE_INDICATORS = [
    "pending", "in progress", "incomplete", "not yet performed", "not yet", "not started",
    "scheduled for phase", "scheduled", "awaiting chamber", "awaiting", "remaining", "deferred", "postponed",
    "partially", "to be completed", "planned", "characterization not yet",
]

# Generic restriction wording in datasheets that may contradict a specification
DATASHEET_RESTRICTION_INDICATORS = [
    "not supported", "does not support", "exceeds maximum", "exceeds the maximum",
    "restricted to", "violates manufacturer warranty", "limited to", "derating", "derated", "incompatible", "insufficient",
    "must not", "limitation",
]

# Generic passing-verdict wording in authoritative test reports
PASS_VERDICT_INDICATORS = ["verdict: pass", "result: pass", "status: pass", "all tests pass", "passed all"]


def _format_source_authority_tag(auth: SourceAuthority, doc_name: str) -> str:
    """Format human-readable authority tag for evidence prompts."""
    tag_map = {
        "EMPIRICAL_TEST": "[SOURCE: EMPIRICAL TEST REPORT / LAB VALIDATION RECORD]",
        "QUALIFICATION_TEST": "[SOURCE: QUALIFICATION / ENVIRONMENTAL TEST REPORT]",
        "VALIDATION_REPORT": "[SOURCE: FUNCTIONAL SAFETY / SYSTEM VALIDATION REPORT]",
        "COMPLIANCE_MATRIX": "[SOURCE: COMPLIANCE TRACKING MATRIX]",
        "DATASHEET": "[SOURCE: COMPONENT DATASHEET - HARDWARE RATINGS]",
        "ARCHITECTURE_SPEC": "[SOURCE: ARCHITECTURE SPECIFICATION / DESIGN INTENTION]",
        "SIMULATION": "[SOURCE: THEORETICAL SIMULATION / SPICE / CFD / MATLAB - NON-EMPIRICAL]",
        "CALCULATION": "[SOURCE: ANALYTICAL CALCULATION / ESTIMATE - NON-EMPIRICAL]",
        "INSPECTION": "[SOURCE: DESIGN / SCHEMATIC INSPECTION RECORD]",
        "UNKNOWN": "[SOURCE: UNCLASSIFIED DOCUMENT]",
    }
    return tag_map.get(auth, "[SOURCE: UNCLASSIFIED DOCUMENT]")


def build_verification_prompt(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
) -> tuple[str, str]:
    """Construct a rigorous, structured compliance auditing prompt with qualification annotations."""
    system_instruction = (
        "You are a formal compliance and quality assurance auditor for safety-critical automotive systems. "
        "Evaluate technical requirements against evidence excerpts with zero assumptions, strict condition checking, "
        "and exact parameter comparisons."
    )

    quals = qualify_evidence_chunks(contract, evidence_chunks)
    formatted_evidence = []
    for i, (q, c) in enumerate(zip(quals, evidence_chunks), 1):
        doc_name = c.get("document_name", "Document")
        page = c.get("page_number")
        page_str = f", Page {page}" if page else ""
        content = _isolate_relevant_passage(
            (c.get("content") or c.get("quote") or "").strip(), contract
        )
        anno = format_qualification_annotation(q)
        formatted_evidence.append(
            f"--- [Evidence Excerpt #{i} ({q.evidence_id}): {doc_name}{page_str}] ---\n{anno}\n{content}\n"
        )

    evidence_block = "\n".join(formatted_evidence) if formatted_evidence else "[No evidence retrieved]"

    cond_descriptions = []
    if contract.atomic_conditions:
        for c in contract.atomic_conditions:
            cond_descriptions.append(f"  - Condition [{c.condition_id}]: {c.description or c.parameter} ({c.operator or ''} {c.threshold or c.max_value or c.min_value or ''} {c.unit or ''})")
    else:
        cond_descriptions.append(f"  - Primary Clause: {contract.title}")

    prompt = f"""Requirement to Verify:
- Requirement ID: {contract.req_code}
- Title: {contract.title}
- Category: {contract.category}
- Scope / Entity: {contract.scope or 'System'}
- Required Verification Method: {contract.verification_method or 'physical_test'}
- Specification Clause: {contract.raw_text}
- Atomic Conditions:
{chr(10).join(cond_descriptions)}

Retrieved Technical Evidence:
{evidence_block}

Auditing Protocol & Verification Rules:

STEP 1: EVIDENCE ATTRIBUTION & RELEVANCE CHECK (Filter Similarity Noise)
- For each retrieved excerpt, evaluate whether it provides DIRECT verification evidence for the target requirement and its specified entity/subsystem, or if it was fetched merely due to keyword/vector similarity.
- HIERARCHICAL SCOPE vs COMPONENT CONTEXT:
  * If an excerpt describes an internal component/sub-circuit rating from a component datasheet, while a primary System-Level Physical Test Report proves that the fully integrated system successfully operated across the entire required operational envelope, the internal component rating represents implementation detail and does NOT restrict or invalidate the verified integrated system capability.
  * If an excerpt describes an intentional fault-injection or safety stress test (e.g., injecting an out-of-range stimulus or simulated fault to verify that protective shutdown/reaction mechanisms execute within required latency), this proves functional safety protective compliance, NOT a specification breach or contradiction.

STEP 2: CONDITION EVALUATION & COMPLIANCE RULES
1. NUMERIC OPERATING ENVELOPE (SUPERSET PROOF):
   - When verifying an operating capability span [Rmin, Rmax], any empirical test envelope [Tmin, Tmax] where Tmin <= Rmin and Tmax >= Rmax (i.e. the tested range fully encompasses the required operational bounds) provides mathematical proof of capability and is PROVEN / SUPPORTED.
   - A nominal target T with an explicit ± tolerance d is ONE composite interval [T-d, T+d]; do not require the observation to equal exactly T.
2. DOCUMENT AUTHORITY & MODALITY DISCIPLINE:
   - When a requirement mandates physical laboratory/bench testing ('physical_test'), theoretical simulations (MATLAB, SPICE, CFD, Simulink), analytical calculations (FMEDA, formulas), or architecture drawings provide 0% empirical proof.
   - You must NOT mark conditions as 'PROVEN' or 'PENDING' based on simulation or calculation evidence when physical test is required. Relevant but method-incompatible evidence is 'INCONCLUSIVE' and the overall requirement is 'UNKNOWN' (NOT 'PARTIAL', NOT 'SUPPORTED').
   - Reserve 'PARTIAL' strictly for when QUALIFIED empirical lab testing directly addressed the condition but covered an incomplete operating envelope or subset. A compliance matrix, design, simulation, or calculation alone is UNKNOWN, not PARTIAL.
   - If the requirement explicitly permits or specifies verification by simulation/analysis, simulation evidence is acceptable.
3. COMPLIANCE MATRIX STATUS:
   - If an official compliance tracking matrix explicitly records 'NOT STARTED', 'MISSING', or 'TEST PENDING' for this requirement, the status is 'MISSING' (condition status: 'UNTESTED').
4. COMPOUND CONDITIONS:
   - For multi-condition requirements, evaluate each condition in `condition_results` using exactly one state: PROVEN (qualified evidence establishes it), FAILED (qualified/relevant hard evidence contradicts it), PENDING (qualified empirical work directly covers only part of it), UNTESTED (no relevant verification evidence exists), or INCONCLUSIVE (relevant evidence exists but has insufficient authority, method, scope, parameter alignment, or detail).
   - JUDGE EVERY CONDITION INDEPENDENTLY. A sibling condition's missing range, pending test, or failure changes the final requirement status but must never downgrade an independently satisfied condition.
   - PENDING describes partial empirical coverage of THIS condition only. Never use PENDING merely because the overall requirement is PARTIAL. If this condition is fully satisfied by qualified evidence, return PROVEN even when another condition remains incomplete.
   - For every condition, also return the exact observed parameter/value/unit, `evidence_value_role` (OBSERVED, REQUIRED_OR_PLANNED, STATUS_ONLY, NOT_ADDRESSED, or UNCLEAR), and `relationship` (SATISFIES, VIOLATES, PARTIAL_COVERAGE, NOT_ADDRESSED, or UNCLEAR).
   - A required, target, planned, scheduled, pending, or not-yet-tested value is NOT an observation. Never use it as measured proof. Set `evidence_value_role` to REQUIRED_OR_PLANNED.
   - Use `observed_min_value` and `observed_max_value` for an observed range. Use `observed_value` for a scalar, boolean, or categorical observation. Copy the observed unit exactly.
   - Final status: 'SUPPORTED' only if ALL conditions PROVEN; 'PARTIAL' if some PROVEN and some PENDING/UNTESTED; 'CONFLICT' if any condition FAILED; 'MISSING' if no evidence / NOT STARTED; 'UNKNOWN' if only simulation / non-authoritative.
   - Every PROVEN, FAILED, or PENDING condition MUST include at least one evidence ID (E1, E2, ...) and a verbatim quote from that evidence. Never return an attributed condition status without both fields.
   - A local failure, violation, leakage, exceeded limit, or lower achieved rating dominates an earlier PASS word from a different sub-test in the same excerpt.

Return your evaluation strictly as a valid JSON object matching the VerificationAnalysisResult schema.
"""
    return prompt, system_instruction


def _match_indicator(text_lower: str, indicators: list[str]) -> Optional[str]:
    """Return the first indicator phrase present in the text, if any."""
    for ind in indicators:
        if ind in text_lower:
            return ind
    return None


def rule_based_multi_condition_verification(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    spec_doc_names: Optional[set[str]] = None,
) -> VerificationAnalysisResult:
    """Deterministic rule-based evaluation using evidence qualification and python-owned verdict aggregation."""
    non_spec_chunks = [
        c for c in evidence_chunks
        if (not spec_doc_names or c.get("document_name") not in spec_doc_names)
        and not any(k in c.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS)
    ]

    # 1. Empty candidate set -> MISSING
    if not non_spec_chunks:
        return VerificationAnalysisResult(
            status="MISSING",
            confidence=95,
            reason=f"No independent test report, datasheet, or compliance matrix was found for {contract.req_code}.",
            requirement_conditions=[],
            condition_results=[],
        )

    # 2. Check for explicit compliance matrix status for this requirement
    evidence_absent = False
    for c in non_spec_chunks:
        doc_name = c.get("document_name", "").lower()
        if any(k in doc_name for k in COMPLIANCE_MATRIX_KEYWORDS):
            c_text = c.get("content", "")
            if contract.req_code.upper() in c_text.upper():
                c_lower = c_text.lower()
                if "not started" in c_lower or "missing" in c_lower or "not tested" in c_lower:
                    m = re.search(r"([^.\n]*(?:not started|missing|not tested)[^.\n]*)", c_text, re.IGNORECASE)
                    snippet = m.group(1).strip() if m else "Status: Not Started"
                    evidence_absent = True
                    return VerificationAnalysisResult(
                        status="MISSING",
                        confidence=95,
                        reason=f"Compliance matrix explicitly marks {contract.req_code} as 'Not Started' / missing evidence.",
                        highlight=snippet,
                    )
                if "in progress" in c_lower:
                    m = re.search(r"([^.\n]*in progress[^.\n]*)", c_text, re.IGNORECASE)
                    snippet = m.group(1).strip() if m else "Status: In Progress"
                    return VerificationAnalysisResult(
                        status="PARTIAL",
                        confidence=92,
                        reason=f"Compliance matrix records {contract.req_code} qualification as 'In Progress'.",
                        highlight=snippet,
                    )
                if "fail" in c_lower:
                    return VerificationAnalysisResult(
                        status="CONFLICT",
                        confidence=95,
                        reason=f"Compliance matrix records formal qualification test failure for {contract.req_code}.",
                        highlight="FAIL",
                    )

    quals = qualify_evidence_chunks(contract, non_spec_chunks)
    # Claim extraction must receive the contract so each multi-requirement page
    # is sliced to the target requirement before numbers and verdicts are read.
    claims = extract_all_evidence_claims(non_spec_chunks, contract)
    cond_results = condition_results_from_claims(contract, claims, quals)
    # In the explicit no-LLM fallback, relevant but unusable evidence is an
    # INCONCLUSIVE condition, not UNTESTED. This keeps the condition-only
    # aggregator pure without losing UNKNOWN semantics in fallback mode.
    for condition_result in cond_results:
        if condition_result.status != "UNTESTED":
            continue
        potentially_relevant = bool(quals)
        if potentially_relevant:
            condition_result.status = "INCONCLUSIVE"
            condition_result.reason = (
                "Relevant evidence exists, but the deterministic no-LLM fallback "
                "cannot establish this condition."
            )
    has_relevant = len(non_spec_chunks) > 0

    status, confidence, reason = aggregate_condition_statuses(
        contract=contract,
        condition_results=cond_results,
        evidence_qualification=quals,
        has_relevant_evidence=has_relevant,
        evidence_absent=evidence_absent,
    )

    return VerificationAnalysisResult(
        status=status,
        confidence=confidence,
        condition_results=cond_results,
        reason=reason,
    )


def build_batch_verification_prompt(
    batch_items: list[dict[str, Any]],
) -> tuple[str, str]:
    """Construct a batch compliance auditing prompt for multiple requirements at once."""
    system_instruction = (
        "You are a formal compliance and quality assurance auditor for safety-critical automotive systems. "
        "Evaluate each technical requirement against its provided technical evidence excerpts with zero assumptions, "
        "strict condition checking, and exact parameter comparisons."
    )

    req_blocks = []
    for i, item in enumerate(batch_items, 1):
        contract: RequirementContract = item["contract"]
        evidence_chunks: list[dict] = item["candidate_chunks"]

        quals = qualify_evidence_chunks(contract, evidence_chunks)
        formatted_evidence = []
        for j, (q, c) in enumerate(zip(quals, evidence_chunks), 1):
            doc_name = c.get("document_name", "Document")
            page = c.get("page_number")
            page_str = f", Page {page}" if page else ""
            content = _isolate_relevant_passage(
                (c.get("content") or c.get("quote") or "").strip(), contract
            )
            anno = format_qualification_annotation(q)
            formatted_evidence.append(
                f"  [Excerpt #{j} ({q.evidence_id}): {doc_name}{page_str}]\n  {anno}\n  {content}"
            )
        evidence_str = "\n".join(formatted_evidence) if formatted_evidence else "  [No independent evidence retrieved]"

        cond_lines = []
        if contract.atomic_conditions:
            for c in contract.atomic_conditions:
                cond_lines.append(f"  - Condition {c.condition_id}: {c.description or c.parameter} ({c.operator or ''} {c.threshold or c.max_value or c.min_value or ''} {c.unit or ''})")
        else:
            cond_lines.append(f"  - Primary Clause: {contract.title}")

        req_blocks.append(
            f"=== REQUIREMENT ITEM #{i} ===\n"
            f"- Req ID: {contract.req_code}\n"
            f"- Title: {contract.title}\n"
            f"- Category: {contract.category}\n"
            f"- Scope / Entity: {contract.scope or 'System'}\n"
            f"- Required Verification Method: {contract.verification_method or 'physical_test'}\n"
            f"- Specification Clause: {contract.raw_text}\n"
            f"- Atomic Conditions to Verify:\n{chr(10).join(cond_lines)}\n"
            f"- Retrieved Technical Evidence:\n{evidence_str}\n"
        )

    prompt = f"""Evaluate the following batch of {len(batch_items)} engineering requirements against their respective retrieved technical evidence excerpts:

{"\n".join(req_blocks)}

Auditing Protocol & Verification Rules for each requirement:

STEP 1: EVIDENCE ATTRIBUTION & RELEVANCE CHECK (Filter Similarity Noise)
- For each requirement, evaluate whether retrieved excerpts provide DIRECT verification evidence for the target requirement and its specified entity/subsystem, or if fetched merely due to keyword/vector similarity.
- HIERARCHICAL SCOPE vs COMPONENT CONTEXT:
  * If an excerpt describes an internal component/sub-circuit rating from a component datasheet, while a primary System-Level Physical Test Report proves that the fully integrated system successfully operated across the entire required operational envelope, the internal component rating represents implementation detail and does NOT restrict or invalidate the verified integrated system capability.
  * If an excerpt describes an intentional fault-injection or safety stress test (e.g., injecting an out-of-range stimulus or simulated fault to verify that protective shutdown/reaction mechanisms execute within required latency), this proves functional safety protective compliance, NOT a specification breach or contradiction.

STEP 2: CONDITION EVALUATION & COMPLIANCE RULES
1. NUMERIC OPERATING ENVELOPE (SUPERSET PROOF):
   - When verifying an operating capability span [Rmin, Rmax], any empirical test envelope [Tmin, Tmax] where Tmin <= Rmin and Tmax >= Rmax (i.e. the tested range fully encompasses the required operational bounds) provides mathematical proof of capability and is PROVEN / SUPPORTED.
   - A nominal target T with an explicit ± tolerance d is ONE composite interval [T-d, T+d]; do not require the observation to equal exactly T.
2. DOCUMENT AUTHORITY & MODALITY DISCIPLINE:
   - When a requirement mandates physical laboratory/bench testing ('physical_test'), theoretical simulations (MATLAB, SPICE, CFD, Simulink), analytical calculations (FMEDA, formulas), or architecture drawings provide 0% empirical proof.
   - You must NOT mark conditions as 'PROVEN' or 'PENDING' based on simulation or calculation evidence when physical test is required. Relevant but method-incompatible evidence is 'INCONCLUSIVE' and the overall requirement is 'UNKNOWN' (NOT 'PARTIAL', NOT 'SUPPORTED').
   - Reserve 'PARTIAL' strictly for when QUALIFIED empirical lab testing directly addressed the condition but covered an incomplete operating envelope or subset. A compliance matrix, design, simulation, or calculation alone is UNKNOWN, not PARTIAL.
   - If the requirement explicitly permits or specifies verification by simulation/analysis, simulation evidence is acceptable.
3. COMPLIANCE MATRIX STATUS:
   - If an official compliance tracking matrix explicitly records 'NOT STARTED', 'MISSING', or 'TEST PENDING' for this requirement, the status is 'MISSING' (condition status: 'UNTESTED').
4. COMPOUND CONDITIONS:
   - For each requirement item, return `condition_results: list[ConditionVerificationResult]` for every defined condition using exactly one state: PROVEN (qualified evidence establishes it), FAILED (qualified/relevant hard evidence contradicts it), PENDING (qualified empirical work directly covers only part of it), UNTESTED (no relevant verification evidence exists), or INCONCLUSIVE (relevant evidence exists but has insufficient authority, method, scope, parameter alignment, or detail).
   - JUDGE EVERY CONDITION INDEPENDENTLY. A sibling condition's missing range, pending test, or failure changes the final requirement status but must never downgrade an independently satisfied condition.
   - PENDING describes partial empirical coverage of THIS condition only. Never use PENDING merely because the overall requirement is PARTIAL. If this condition is fully satisfied by qualified evidence, return PROVEN even when another condition remains incomplete.
   - For every condition, also return the exact observed parameter/value/unit, `evidence_value_role` (OBSERVED, REQUIRED_OR_PLANNED, STATUS_ONLY, NOT_ADDRESSED, or UNCLEAR), and `relationship` (SATISFIES, VIOLATES, PARTIAL_COVERAGE, NOT_ADDRESSED, or UNCLEAR).
   - A required, target, planned, scheduled, pending, or not-yet-tested value is NOT an observation. Never use it as measured proof. Set `evidence_value_role` to REQUIRED_OR_PLANNED.
   - Use `observed_min_value` and `observed_max_value` for an observed range. Use `observed_value` for a scalar, boolean, or categorical observation. Copy the observed unit exactly.
   - Final status: 'SUPPORTED' if all conditions PROVEN; 'PARTIAL' if some PROVEN and some PENDING/UNTESTED; 'CONFLICT' if any condition FAILED or violated; 'MISSING' if no evidence / NOT STARTED; 'UNKNOWN' if only simulation / non-authoritative.
   - Every PROVEN, FAILED, or PENDING condition MUST include at least one evidence ID (E1, E2, ...) and a verbatim quote from that evidence. Never return an attributed condition status without both fields.
   - A local failure, violation, leakage, exceeded limit, or lower achieved rating dominates an earlier PASS word from a different sub-test in the same excerpt.

Respond with a JSON object containing `batch_results: list[BatchVerificationItemResult]` with an item for each requirement.
"""
    return prompt, system_instruction


async def evaluate_batch_verification(
    batch_items: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> dict[str, VerificationAnalysisResult]:
    """Execute batched multi-condition verification with Gemini 3.7 Flash, recomputed in Python."""
    active_model = model or settings.LLM_MODEL
    has_keys = bool(settings.effective_gemini_api_key or settings.effective_databricks_token or settings.effective_openai_api_key)

    results: dict[str, VerificationAnalysisResult] = {}

    if not has_keys or not batch_items:
        for item in batch_items:
            res = rule_based_multi_condition_verification(item["contract"], item["candidate_chunks"])
            results[item["contract"].req_code] = res
        return results

    prompt, system_instruction = build_batch_verification_prompt(batch_items)

    try:
        batch_resp: Optional[BatchVerificationResult] = await generate_structured(
            prompt=prompt,
            response_model=BatchVerificationResult,
            model=active_model,
            system_instruction=system_instruction,
            thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
        )
        if batch_resp and batch_resp.batch_results:
            item_by_code = {it["contract"].req_code: it for it in batch_items}
            for item_res in batch_resp.batch_results:
                it = item_by_code.get(item_res.req_code)
                if it:
                    contract: RequirementContract = it["contract"]
                    cand_chunks: list[dict] = it.get("candidate_chunks", [])
                    quals = qualify_evidence_chunks(contract, cand_chunks)
                    qual_contents = {
                        q.evidence_id: _isolate_relevant_passage(
                            (c.get("content") or c.get("quote") or ""), contract
                        )
                        for q, c in zip(quals, cand_chunks)
                    }
                    provisional = VerificationAnalysisResult(
                        status=item_res.status,
                        confidence=item_res.confidence,
                        condition_results=item_res.condition_results,
                        reason=item_res.reason,
                        highlight=item_res.highlight,
                    )
                    original_conditions = _snapshot_condition_results(provisional.condition_results)
                    provisional._diagnostics = {
                        "decision_source": "llm",
                        "llm_provisional_status": provisional.status,
                        "llm_provisional_confidence": provisional.confidence,
                    }
                    provisional = _audit_llm_condition_metadata(contract, provisional)
                    _record_reconciliation_diagnostics(provisional, original_conditions)
                    # Always recompute final verdict in Python
                    results[item_res.req_code] = finalize_verdict(
                        contract=contract,
                        analysis=provisional,
                        qualifications=quals,
                        qualified_contents=qual_contents,
                    )
    except Exception as ex:
        logger.warning(f"Batch verification LLM call failed: {ex}. Retrying missing items individually.")

    # A malformed item must not discard its valid siblings or trigger semantic
    # guessing. Retry only missing requirements through the single-item LLM
    # path; repeated failures become explicit UNKNOWN operational results.
    for item in batch_items:
        code = item["contract"].req_code
        if code not in results:
            results[code] = await evaluate_requirement_verification(
                contract=item["contract"],
                evidence_chunks=item["candidate_chunks"],
                model=model,
                thinking_level=thinking_level,
                allow_deterministic_fallback=False,
            )

    return results


async def evaluate_requirement_verification(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    spec_doc_names: Optional[set[str]] = None,
    allow_deterministic_fallback: bool = True,
) -> VerificationAnalysisResult:
    """Execute LLM condition reasoning followed only by mechanical aggregation."""
    active_model = model or settings.LLM_MODEL
    has_keys = bool(settings.effective_gemini_api_key or settings.effective_databricks_token or settings.effective_openai_api_key)

    # If no LLM keys are provided, use deterministic rule-based multi-condition engine
    if not has_keys:
        return rule_based_multi_condition_verification(contract, evidence_chunks, spec_doc_names=spec_doc_names)

    prompt, system_instruction = build_verification_prompt(contract, evidence_chunks)

    try:
        result: Optional[VerificationAnalysisResult] = await generate_structured(
            prompt=prompt,
            response_model=VerificationAnalysisResult,
            model=active_model,
            system_instruction=system_instruction,
            thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
        )
        if result and result.status in ("SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT"):
            original_conditions = _snapshot_condition_results(result.condition_results)
            result._diagnostics = {
                "decision_source": "llm",
                "llm_provisional_status": result.status,
                "llm_provisional_confidence": result.confidence,
            }
            quals = qualify_evidence_chunks(contract, evidence_chunks, spec_doc_names=spec_doc_names)
            qual_contents = {
                q.evidence_id: _isolate_relevant_passage(
                    (c.get("content") or c.get("quote") or ""), contract
                )
                for q, c in zip(quals, evidence_chunks)
            }
            result = _audit_llm_condition_metadata(contract, result)
            _record_reconciliation_diagnostics(result, original_conditions)
            # Python owns only the mechanical condition-to-requirement mapping.
            return finalize_verdict(
                contract=contract,
                analysis=result,
                qualifications=quals,
                qualified_contents=qual_contents,
            )
    except Exception as ex:
        logger.warning(f"Structured LLM verification call failed: {ex}.")

    if allow_deterministic_fallback:
        return rule_based_multi_condition_verification(contract, evidence_chunks, spec_doc_names=spec_doc_names)

    unavailable = VerificationAnalysisResult(
        status="UNKNOWN",
        confidence=0,
        condition_results=[
            ConditionVerificationResult(
                condition_id=condition.condition_id,
                description=condition.description or condition.parameter,
                status="INCONCLUSIVE",
                reason="LLM verification response was unavailable or invalid after retry.",
            )
            for condition in contract.atomic_conditions
            if condition.mandatory
        ],
        reason="LLM verification response was unavailable or invalid after retry; no semantic fallback was guessed.",
    )
    unavailable._diagnostics = {
        "decision_source": "llm_error",
        "final_status": "UNKNOWN",
    }
    return unavailable
