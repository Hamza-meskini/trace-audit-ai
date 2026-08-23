"""Structured Multi-Condition Verification Reasoner Service.

Combines deterministic Python comparison with structured LLM condition reasoning
to evaluate complex compliance, multi-axis profiles, and cross-document contradictions.
"""

import re
import logging
from typing import Optional, Any

from app.config import settings
from app.schemas.contract import RequirementContract, parse_requirement_contract
from app.schemas.claim import EvidenceClaim, extract_all_evidence_claims
from app.schemas.verification_result import (
    RequirementCondition,
    EvidenceFinding,
    VerificationAnalysisResult,
)
from app.services.llm_client import generate_structured
from app.services.units import convert_value, are_units_compatible

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
COMPLIANCE_MATRIX_KEYWORDS = ("matrix", "verification")

# Generic incomplete-verification wording found in test reports and matrices
PARTIAL_EVIDENCE_INDICATORS = [
    "pending", "in progress", "incomplete", "not yet", "not started",
    "scheduled", "awaiting", "remaining", "deferred", "postponed",
    "partially", "to be completed", "planned",
]

# Document-name markers for supplier component documents
DATASHEET_DOC_KEYWORDS = ("datasheet", "data sheet", "supplier", "oem", "ds-")

# Generic restriction wording in datasheets that may contradict a specification
DATASHEET_RESTRICTION_INDICATORS = [
    "not supported", "does not support", "exceeds maximum", "exceeds the maximum",
    "restricted", "derating", "derated", "incompatible", "insufficient",
    "must not", "limitation",
]

# Generic passing-verdict wording in authoritative test reports
PASS_VERDICT_INDICATORS = ["verdict: pass", "result: pass", "status: pass", "all tests pass", "passed all"]


def _infer_source_authority(doc_name: str, content: str) -> str:
    """Classify evidence chunk source authority level."""
    dn = doc_name.lower()
    ct = content.lower()
    if any(k in dn or k in ct for k in ("simul", "spice", "cfd", "matlab", "ltspice", "model predicts", "modeling", "theoretical")):
        return "[SOURCE: THEORETICAL SIMULATION / CALCULATION - NON-EMPIRICAL]"
    elif any(k in dn for k in ("arch", "interface", "arch_spec")) and not any(k in dn for k in ("test", "report", "lab")):
        return "[SOURCE: ARCHITECTURE SPECIFICATION / DESIGN INTENTION]"
    elif any(k in dn for k in ("datasheet", "ds-", "oem", "supplier")):
        return "[SOURCE: COMPONENT DATASHEET - HARDWARE RATINGS]"
    elif any(k in dn for k in ("matrix", "verification_matrix")):
        return "[SOURCE: COMPLIANCE TRACKING MATRIX]"
    else:
        return "[SOURCE: EMPIRICAL TEST REPORT / LAB VALIDATION RECORD]"


def build_verification_prompt(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
) -> tuple[str, str]:
    """Construct a rigorous, structured compliance auditing prompt with source authority tags."""
    system_instruction = (
        "You are a formal compliance and quality assurance auditor for safety-critical hardware/software systems. "
        "Evaluate technical requirements against evidence excerpts with zero assumptions, strict condition checking, "
        "and exact parameter comparisons."
    )

    formatted_evidence = []
    for i, c in enumerate(evidence_chunks, 1):
        doc_name = c.get("document_name", "Document")
        page = c.get("page_number")
        page_str = f", Page {page}" if page else ""
        content = (c.get("content") or c.get("quote") or "").strip()
        auth_tag = _infer_source_authority(doc_name, content)
        formatted_evidence.append(
            f"--- [Evidence Excerpt #{i}: {doc_name}{page_str}] ---\n{auth_tag}\n{content}\n"
        )

    evidence_block = "\n".join(formatted_evidence) if formatted_evidence else "[No evidence retrieved]"

    prompt = f"""Requirement to Verify:
- Requirement ID: {contract.req_code}
- Title: {contract.title}
- Category: {contract.category}
- Specification Clause: {contract.raw_text}

Retrieved Technical Evidence:
{evidence_block}

Auditing Rules & Constraints:
1. Evaluate each retrieved evidence excerpt INDEPENDENTLY and respect Document Authority:
   - EMPIRICAL TEST REPORT: Direct proof of empirical testing.
   - COMPLIANCE MATRIX: Formal verification tracking record (PASS, IN PROGRESS, NOT STARTED, FAIL).
   - COMPONENT DATASHEET: Hardware component electrical/thermal operating limits.
   - THEORETICAL SIMULATION / ARCHITECTURE SPEC: Design intentions and mathematical predictions only.
2. SOURCE AUTHORITY RULE:
   - Theoretical simulations (SPICE, CFD, MATLAB, mathematical models) and Architecture Specifications describe design intentions, NOT empirical test proof.
   - If the ONLY available evidence for a requirement is theoretical simulation, calculation, or architecture specification clause, you MUST classify as 'UNKNOWN'.
3. ENTITY & SCOPE RULE:
   - If a supplier component datasheet declares a component maximum rating (e.g. ASIC voltage limit), and the requirement is for the overall system (e.g. BCU Pack operating voltage), and system test reports show successful system testing across the full range, classify as 'SUPPORTED'.
4. For numerical / technical requirements, explicitly compare:
   - Parameter name and unit
   - Required value or operating range
   - Observed tested value or operating range
   - A tested operating range [Tmin, Tmax] that fully encompasses the required range [Rmin, Rmax] (Tmin <= Rmin and Tmax >= Rmax) is SUPPORTED.
5. Multi-Condition Classification:
   - Classify as 'SUPPORTED' ONLY if EVERY mandatory condition is explicitly verified by empirical test/compliance records.
   - Classify as 'PARTIAL' if some conditions are verified by empirical testing but others are pending, scheduled, in progress, or unmeasured.
   - Classify as 'CONFLICT' if empirical evidence shows a test failure, parameter violation, or an incompatible component limit.
   - Classify as 'MISSING' if no retrieved excerpt contains meaningful technical information for this requirement, or if explicitly marked NOT STARTED.
   - Classify as 'UNKNOWN' if only theoretical simulation, calculation, or architecture intention is provided, or evidence is inconclusive.
6. Conservative Factuality:
   - Never infer or assume values or conditions not explicitly stated in the evidence excerpts.
   - Cite the exact quote snippet in your findings.

Return your evaluation as a structured JSON object.
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
    """Deterministic rule-based evaluation for multi-condition and partial evidence.

    Uses only generic engineering-document indicators (statuses, pending wording,
    restriction wording, pass verdicts) so it generalizes beyond any single dataset.
    """
    non_spec_chunks = [
        c for c in evidence_chunks
        if (not spec_doc_names or c.get("document_name") not in spec_doc_names)
        and not any(k in c.get("document_name", "").lower() for k in SPEC_DOC_KEYWORDS)
    ]

    if not non_spec_chunks:
        return VerificationAnalysisResult(
            status="MISSING",
            confidence=95,
            reason=f"No independent test report, datasheet, or compliance matrix was found for {contract.req_code}.",
        )

    def _snippet_for(c_text: str, indicator: str) -> str:
        m = re.search(rf"([^.\n]*?{re.escape(indicator)}[^.\n]*)", c_text, re.IGNORECASE)
        return m.group(1).strip() if m else c_text[:120]

    # 1. Check for explicit compliance/verification matrix status for this requirement
    for c in non_spec_chunks:
        doc_name = c.get("document_name", "").lower()
        if any(k in doc_name for k in COMPLIANCE_MATRIX_KEYWORDS):
            c_text = c.get("content", "")
            if contract.req_code.upper() in c_text.upper():
                c_lower = c_text.lower()
                if "not started" in c_lower or "missing" in c_lower:
                    m = re.search(r"([^.\n]*(?:not started|missing)[^.\n]*)", c_text, re.IGNORECASE)
                    snippet = m.group(1).strip() if m else "Status: Not Started"
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
                        reason=f"Compliance matrix records {contract.req_code} as 'In Progress'.",
                        highlight=snippet,
                    )

    # 2. Check for generic partial / pending verification indicators in test reports
    for c in non_spec_chunks:
        c_text = c.get("content", "")
        c_lower = c_text.lower()
        indicator = _match_indicator(c_lower, PARTIAL_EVIDENCE_INDICATORS)
        if indicator:
            snippet = _snippet_for(c_text, indicator)
            return VerificationAnalysisResult(
                status="PARTIAL",
                confidence=90,
                reason=f"Evidence indicates partial verification with remaining activities pending: '{snippet}'.",
                highlight=snippet,
            )

    # 3. Check for component datasheet restriction contradictions
    for c in non_spec_chunks:
        doc_name = c.get("document_name", "").lower()
        if any(k in doc_name for k in DATASHEET_DOC_KEYWORDS):
            c_text = c.get("content", "")
            c_lower = c_text.lower()
            indicator = _match_indicator(c_lower, DATASHEET_RESTRICTION_INDICATORS)
            if indicator:
                snippet = _snippet_for(c_text, indicator)
                return VerificationAnalysisResult(
                    status="CONFLICT",
                    confidence=95,
                    reason=f"Datasheet restriction directly contradicts specification requirements: '{snippet}'.",
                    highlight=snippet,
                )

    # 4. Check if all available non-spec chunks are theoretical simulation or architecture specification
    is_only_simulation = all(
        any(k in c.get("document_name", "").lower() or k in c.get("content", "").lower() for k in ("simul", "spice", "cfd", "matlab", "ltspice", "model predicts", "modeling", "theoretical", "arch", "interface", "arch_spec"))
        for c in non_spec_chunks
    )
    if is_only_simulation:
        return VerificationAnalysisResult(
            status="UNKNOWN",
            confidence=85,
            reason=f"Evidence for {contract.req_code} consists only of theoretical simulations, calculations, or architecture specifications without empirical test data.",
        )

    # 5. Check for explicit PASS verdicts in authoritative test reports
    for c in non_spec_chunks:
        c_text = c.get("content", "")
        c_lower = c_text.lower()
        indicator = _match_indicator(c_lower, PASS_VERDICT_INDICATORS)
        if indicator:
            m = re.search(r"([^.\n]*(?:pass|passed)[^.\n]*)", c_text, re.IGNORECASE)
            snippet = m.group(1).strip() if m else c_text[:120]
            return VerificationAnalysisResult(
                status="SUPPORTED",
                confidence=95,
                reason=f"Authoritative test report in {c.get('document_name')} records an explicit passing verdict.",
                highlight=snippet,
            )

    return VerificationAnalysisResult(
        status="UNKNOWN",
        confidence=70,
        reason="Evidence is qualitative without deterministic numeric or verdict proof.",
    )


def build_batch_verification_prompt(
    batch_items: list[dict[str, Any]],
) -> tuple[str, str]:
    """Construct a batch compliance auditing prompt for multiple requirements at once."""
    system_instruction = (
        "You are a formal compliance and quality assurance auditor for safety-critical hardware/software systems. "
        "Evaluate each technical requirement against its provided technical evidence excerpts with zero assumptions, "
        "strict multi-condition checking, and exact parameter comparisons."
    )

    req_blocks = []
    for i, item in enumerate(batch_items, 1):
        contract: RequirementContract = item["contract"]
        evidence_chunks: list[dict] = item["candidate_chunks"]

        formatted_evidence = []
        for j, c in enumerate(evidence_chunks, 1):
            doc_name = c.get("document_name", "Document")
            page = c.get("page_number")
            page_str = f", Page {page}" if page else ""
            content = (c.get("content") or c.get("quote") or "").strip()
            auth_tag = _infer_source_authority(doc_name, content)
            formatted_evidence.append(
                f"  [Excerpt #{j}: {doc_name}{page_str}]\n  {auth_tag}\n  {content}"
            )
        evidence_str = "\n".join(formatted_evidence) if formatted_evidence else "  [No independent evidence retrieved]"

        req_blocks.append(
            f"=== REQUIREMENT ITEM #{i} ===\n"
            f"- Req ID: {contract.req_code}\n"
            f"- Title: {contract.title}\n"
            f"- Category: {contract.category}\n"
            f"- Specification Clause: {contract.raw_text}\n"
            f"- Retrieved Technical Evidence:\n{evidence_str}\n"
        )

    prompt = f"""Evaluate the following batch of {len(batch_items)} engineering requirements against their respective retrieved technical evidence excerpts:

{"\n".join(req_blocks)}

Auditing Rules for each requirement:
1. Evaluate each requirement INDEPENDENTLY and respect Document Authority:
   - EMPIRICAL TEST REPORT: Direct proof of empirical testing.
   - COMPLIANCE MATRIX: Formal verification tracking record (PASS, IN PROGRESS, NOT STARTED, FAIL).
   - COMPONENT DATASHEET: Hardware component electrical/thermal operating limits.
   - THEORETICAL SIMULATION / ARCHITECTURE SPEC: Design intentions and mathematical predictions only.
2. SOURCE AUTHORITY RULE:
   - Theoretical simulations (SPICE, CFD, MATLAB, mathematical calculations) and Architecture Specifications describe design intentions and mathematical models only. They CANNOT satisfy physical/functional testing requirements.
   - If the ONLY available evidence for a requirement is theoretical simulation, calculation, or architecture specification clause, you MUST classify as 'UNKNOWN'.
3. ENTITY & SCOPE RULE:
   - If a supplier component datasheet declares a component maximum rating (e.g. ASIC voltage limit), and the requirement is for the overall system (e.g. BCU Pack operating voltage), and system test reports show successful system testing across the full range, classify as 'SUPPORTED'.
4. For numerical / technical requirements:
   - A tested operating range [Tmin, Tmax] that fully encompasses the required range [Rmin, Rmax] (Tmin <= Rmin and Tmax >= Rmax) is SUPPORTED.
5. Multi-Condition Classification:
   - Classify as 'SUPPORTED' ONLY if EVERY mandatory condition is explicitly verified by empirical test/compliance records.
   - Classify as 'PARTIAL' if some conditions are verified by empirical testing but others are pending, scheduled, in progress, or unmeasured.
   - Classify as 'CONFLICT' if empirical evidence shows a test failure, parameter violation, or an incompatible component limit.
   - Classify as 'MISSING' if no retrieved excerpt contains meaningful technical information for this requirement, or matrix says NOT STARTED.
   - Classify as 'UNKNOWN' if only theoretical simulation, calculation, or architecture intention is provided, or evidence is inconclusive.

Respond with a JSON object containing `batch_results: list[BatchVerificationItemResult]` with an item for each requirement.
"""
    return prompt, system_instruction


async def evaluate_batch_verification(
    batch_items: list[dict[str, Any]],
    model: Optional[str] = None,
    thinking_level: Optional[str] = None,
) -> dict[str, VerificationAnalysisResult]:
    """Execute batched multi-condition verification with Gemini 3.7 Flash."""
    from app.schemas.verification_result import BatchVerificationResult

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
            for item_res in batch_resp.batch_results:
                results[item_res.req_code] = VerificationAnalysisResult(
                    status=item_res.status,
                    confidence=item_res.confidence,
                    reason=item_res.reason,
                    highlight=item_res.highlight,
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
    """Execute structured multi-condition verification using LLM or deterministic fallback."""
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
            return result
    except Exception as ex:
        logger.warning(f"Structured LLM verification call failed: {ex}. Falling back to deterministic engine.")

    return rule_based_multi_condition_verification(contract, evidence_chunks, spec_doc_names=spec_doc_names)
