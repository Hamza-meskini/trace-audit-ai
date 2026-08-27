"""Structured Multi-Condition Verification Reasoner Service.

Combines deterministic Python comparison with structured LLM condition reasoning
to evaluate complex compliance, multi-axis profiles, and cross-document contradictions.
"""

import re
import logging
from typing import Optional, Any

from app.config import settings
from app.schemas.contract import RequirementContract, parse_requirement_contract, AtomicConditionContract
from app.schemas.claim import (
    EvidenceClaim,
    extract_all_evidence_claims,
    classify_source_authority,
    SourceAuthority,
)
from app.schemas.verification_result import (
    RequirementCondition,
    ConditionVerificationResult,
    EvidenceFinding,
    VerificationAnalysisResult,
    BatchVerificationItemResult,
    BatchVerificationResult,
)
from app.services.llm_client import generate_structured
from app.services.units import convert_value, are_units_compatible
from app.schemas.evidence_qualification import EvidenceQualification
from app.services.evidence_qualification import (
    qualify_evidence,
    qualify_evidence_chunks,
    format_qualification_annotation,
    has_qualified_evidence,
)
from app.services.verdict_aggregator import (
    aggregate_condition_statuses,
    finalize_verdict,
    condition_results_from_claims,
    mandatory_conditions,
)

logger = logging.getLogger("traceaudit.verifier")

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
        content = (c.get("content") or c.get("quote") or "").strip()
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
2. DOCUMENT AUTHORITY & MODALITY DISCIPLINE:
   - When a requirement mandates physical laboratory/bench testing ('physical_test'), theoretical simulations (MATLAB, SPICE, CFD, Simulink), analytical calculations (FMEDA, formulas), or architecture drawings provide 0% empirical proof.
   - You must NOT mark conditions as 'PROVEN' or 'PENDING' based on simulation or calculation evidence when physical test is required. Mark condition status as 'UNTESTED' and overall requirement status as 'UNKNOWN' (NOT 'PARTIAL', NOT 'SUPPORTED').
   - Reserve 'PARTIAL' strictly for when actual empirical lab testing was conducted across an incomplete operating envelope or subset of conditions.
   - If the requirement explicitly permits or specifies verification by simulation/analysis, simulation evidence is acceptable.
3. COMPLIANCE MATRIX STATUS:
   - If an official compliance tracking matrix explicitly records 'NOT STARTED', 'MISSING', or 'TEST PENDING' for this requirement, the status is 'MISSING' (condition status: 'UNTESTED').
4. COMPOUND CONDITIONS:
   - For multi-condition requirements, evaluate each condition in `condition_results` with status: 'PROVEN', 'FAILED', 'PENDING', or 'UNTESTED'.
   - Final status: 'SUPPORTED' only if ALL conditions PROVEN; 'PARTIAL' if some PROVEN and some PENDING/UNTESTED; 'CONFLICT' if any condition FAILED; 'MISSING' if no evidence / NOT STARTED; 'UNKNOWN' if only simulation / non-authoritative.

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
    claims = extract_all_evidence_claims(non_spec_chunks)
    cond_results = condition_results_from_claims(contract, claims, quals)
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
            content = (c.get("content") or c.get("quote") or "").strip()
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
2. DOCUMENT AUTHORITY & MODALITY DISCIPLINE:
   - When a requirement mandates physical laboratory/bench testing ('physical_test'), theoretical simulations (MATLAB, SPICE, CFD, Simulink), analytical calculations (FMEDA, formulas), or architecture drawings provide 0% empirical proof.
   - You must NOT mark conditions as 'PROVEN' or 'PENDING' based on simulation or calculation evidence when physical test is required. Mark condition status as 'UNTESTED' and overall requirement status as 'UNKNOWN' (NOT 'PARTIAL', NOT 'SUPPORTED').
   - Reserve 'PARTIAL' strictly for when actual empirical lab testing was conducted across an incomplete operating envelope or subset of conditions.
   - If the requirement explicitly permits or specifies verification by simulation/analysis, simulation evidence is acceptable.
3. COMPLIANCE MATRIX STATUS:
   - If an official compliance tracking matrix explicitly records 'NOT STARTED', 'MISSING', or 'TEST PENDING' for this requirement, the status is 'MISSING' (condition status: 'UNTESTED').
4. COMPOUND CONDITIONS:
   - For each requirement item, return `condition_results: list[ConditionVerificationResult]` for each defined condition with status: 'PROVEN', 'FAILED', 'PENDING', or 'UNTESTED'.
   - Final status: 'SUPPORTED' if all conditions PROVEN; 'PARTIAL' if some PROVEN and some PENDING/UNTESTED; 'CONFLICT' if any condition FAILED or violated; 'MISSING' if no evidence / NOT STARTED; 'UNKNOWN' if only simulation / non-authoritative.

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
                    qual_contents = {q.evidence_id: (c.get("content") or c.get("quote") or "") for q, c in zip(quals, cand_chunks)}
                    has_relevant = any(not any(k in c.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS) for c in cand_chunks)

                    # Check if compliance matrix explicitly records NOT STARTED / PENDING
                    evidence_absent = any(
                        ("not started" in (c.get("content") or "").lower() or "missing" in (c.get("content") or "").lower() or "not tested" in (c.get("content") or "").lower())
                        and any(k in c.get("document_name", "").lower() for k in COMPLIANCE_MATRIX_KEYWORDS)
                        and contract.req_code.upper() in (c.get("content") or "").upper()
                        for c in cand_chunks
                    )

                    provisional = VerificationAnalysisResult(
                        status=item_res.status,
                        confidence=item_res.confidence,
                        condition_results=item_res.condition_results,
                        reason=item_res.reason,
                        highlight=item_res.highlight,
                    )
                    # Always recompute final verdict in Python
                    results[item_res.req_code] = finalize_verdict(
                        contract=contract,
                        analysis=provisional,
                        qualifications=quals,
                        qualified_contents=qual_contents,
                        has_relevant_evidence=has_relevant,
                        evidence_absent=evidence_absent,
                    )
    except Exception as ex:
        logger.warning(f"Batch verification LLM call failed: {ex}. Falling back to rule-based verification.")

    # Fill in any missing items with deterministic rule engine
    for item in batch_items:
        code = item["contract"].req_code
        if code not in results:
            results[code] = rule_based_multi_condition_verification(item["contract"], item["candidate_chunks"])

    return results


async def evaluate_requirement_verification(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    spec_doc_names: Optional[set[str]] = None,
) -> VerificationAnalysisResult:
    """Execute structured multi-condition verification using LLM, finalized deterministically in Python."""
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
            quals = qualify_evidence_chunks(contract, evidence_chunks, spec_doc_names=spec_doc_names)
            qual_contents = {q.evidence_id: (c.get("content") or c.get("quote") or "") for q, c in zip(quals, evidence_chunks)}
            has_relevant = any(not any(k in c.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS) for c in evidence_chunks)
            # Python recomputes and owns final status
            return finalize_verdict(
                contract=contract,
                analysis=result,
                qualifications=quals,
                qualified_contents=qual_contents,
                has_relevant_evidence=has_relevant,
            )
    except Exception as ex:
        logger.warning(f"Structured LLM verification call failed: {ex}. Falling back to deterministic engine.")

    return rule_based_multi_condition_verification(contract, evidence_chunks, spec_doc_names=spec_doc_names)

