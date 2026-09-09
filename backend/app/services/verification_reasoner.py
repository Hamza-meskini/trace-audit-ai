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
    CitationGroundingResult,
    ConditionVerificationResult,
    EvidenceSpanReference,
    SemanticAdjudicationResult,
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
    locate_exact_quote_span,
)

logger = logging.getLogger("traceaudit.verifier")


def _condition_line(condition: AtomicConditionContract) -> str:
    target = condition.threshold
    if target is None:
        if condition.min_value is not None and condition.max_value is not None:
            target = f"{condition.min_value}..{condition.max_value}"
        elif condition.max_value is not None:
            target = condition.max_value
        elif condition.min_value is not None:
            target = condition.min_value
        else:
            target = ""
    visual = "; VISUAL EVIDENCE REQUIRED" if condition.requires_visual_evidence else ""
    return (
        f"  - Condition [{condition.condition_id}]: {condition.description or condition.parameter} "
        f"({condition.operator or ''} {target} {condition.unit or ''}; "
        f"role={condition.condition_role}; mandatory={condition.mandatory}{visual})"
    )


def _logic_line(contract: RequirementContract) -> str:
    logic = contract.logic
    if logic.operator == "IF_THEN":
        return (
            f"IF_THEN: if={logic.if_condition_id}; "
            f"then={','.join(logic.then_condition_ids)}"
        )
    return f"{logic.operator}: {','.join(logic.condition_ids)}"


def _canonical_condition_results(
    contract: RequirementContract,
    results: list[ConditionVerificationResult],
) -> tuple[list[ConditionVerificationResult], list[str]]:
    """Return at most one result for every declared atomic condition."""
    expected_conditions = list(contract.atomic_conditions)
    expected = [condition.condition_id for condition in expected_conditions]
    descriptions = {
        condition.condition_id: condition.description
        for condition in expected_conditions
    }
    remaining = list(results or [])
    canonical: list[ConditionVerificationResult] = []
    missing: list[str] = []
    for condition_id in expected:
        suffix = condition_id.rsplit("-", 1)[-1].lower()
        match_index = next((
            index for index, result in enumerate(remaining)
            if result.condition_id == condition_id
            or (result.condition_id or "").lower() == suffix
            or (result.condition_id or "").lower().endswith(f"-{suffix}")
        ), None)
        if match_index is None:
            missing.append(condition_id)
            continue
        result = remaining.pop(match_index)
        result.condition_id = condition_id
        if not result.description:
            result.description = descriptions.get(condition_id)
        canonical.append(result)
    return canonical, missing


def _fill_missing_conditions(
    contract: RequirementContract,
    results: list[ConditionVerificationResult],
) -> list[ConditionVerificationResult]:
    canonical, missing = _canonical_condition_results(contract, results)
    descriptions = {condition.condition_id: condition.description for condition in contract.atomic_conditions}
    canonical.extend(
        ConditionVerificationResult(
            condition_id=condition_id,
            description=descriptions.get(condition_id),
            status="INCONCLUSIVE",
            evidence_value_role="UNCLEAR",
            relationship="UNCLEAR",
            reason="The LLM omitted this declared condition after a focused retry.",
        )
        for condition_id in missing
    )
    order = {condition.condition_id: index for index, condition in enumerate(contract.atomic_conditions)}
    canonical.sort(key=lambda result: order.get(result.condition_id, len(order)))
    return canonical


def _logic_retry_condition_ids(
    contract: RequirementContract,
    results: list[ConditionVerificationResult],
) -> list[str]:
    """Identify unresolved Boolean gates that deserve one focused LLM pass."""
    by_id = {result.condition_id: result for result in results}
    unresolved = {"PENDING", "UNTESTED", "INCONCLUSIVE"}
    if contract.logic.operator == "ANY_OF":
        governed = list(contract.logic.condition_ids)
        statuses = {(by_id.get(item).status if by_id.get(item) else "UNTESTED") for item in governed}
        if statuses & unresolved:
            # Revisit the whole alternative set so the model can select a
            # proven path and mark genuinely unused branches NOT_APPLICABLE.
            return governed
    if contract.logic.operator == "IF_THEN":
        targets: list[str] = []
        antecedent_id = contract.logic.if_condition_id
        antecedent = by_id.get(antecedent_id) if antecedent_id else None
        if antecedent_id and (antecedent is None or antecedent.status in unresolved):
            targets.append(antecedent_id)
        elif antecedent is not None and antecedent.status == "PROVEN":
            targets.extend(
                condition_id
                for condition_id in contract.logic.then_condition_ids
                if by_id.get(condition_id) is None or by_id[condition_id].status in unresolved
            )
        return targets
    return []


def _focused_logic_retry_prompt(
    base_prompt: str,
    contract: RequirementContract,
    results: list[ConditionVerificationResult],
    target_ids: list[str],
) -> str:
    previous = [
        result.model_dump(exclude_none=True)
        for result in results
        if result.condition_id in set(target_ids)
    ]
    return (
        base_prompt
        + "\n\nFOCUSED REGULATORY-LOGIC REVIEW REQUIRED:\n"
        + f"- Logic: {_logic_line(contract)}\n"
        + f"- Recheck condition IDs: {', '.join(target_ids)}\n"
        + f"- Previous findings: {previous}\n"
        + "Compare operands and measurements directly. A table need not contain an explicit prose conclusion: "
          "for example, observed A=3.4 and B=2.7 proves A>=B. PDF extraction may render subscripts "
          "as spaced labels such as 'V = 1' for V1 and primes as 'V ’ = 1' for V1-prime. "
          "For ANY_OF, identify the demonstrated path and mark genuinely unused alternatives NOT_APPLICABLE. "
          "Return every declared condition exactly once with exact citations."
    )


_NO_EVIDENCE_REASON_MARKERS = (
    "no evidence", "no direct evidence", "not documented", "not provided",
    "does not address", "not addressed", "no test result", "no measurement",
)
_ASSUMPTION_REASON_MARKERS = (
    "implies", "implying", "assume", "assumed", "likely", "suggests",
    "although not explicit", "not explicitly", "presumably", "can be inferred",
)
_NOT_EXECUTED_MARKERS = (
    "not tested", "not measured", "not executed", "not performed",
    "test was not", "measurement is unavailable", "no measurement",
)
_HARD_VIOLATION_MARKERS = (
    "visible leakage", "result: fail", "verdict: fail", "nonconform",
    "non-conform", "violation", "exceeded", "breach", "failed inspection",
)


def _semantic_retry_condition_ids(
    contract: RequirementContract,
    results: list[ConditionVerificationResult],
    evidence_chunks: list[dict[str, Any]],
) -> list[str]:
    """Locate ambiguous first-pass semantics that deserve one LLM recheck.

    This function never changes a status. It catches self-described assumptions,
    UNTESTED/INCONCLUSIVE taxonomy drift, and structurally split forms that show
    both a checklist failure label and an explicit measured/result value.
    """
    targets: set[str] = set()
    evidence_text = "\n".join(
        str(chunk.get("content") or chunk.get("quote") or "")
        for chunk in evidence_chunks
    ).lower()
    visual_interpreted = "visual description:" in evidence_text
    conditions = {
        condition.condition_id: condition
        for condition in contract.atomic_conditions
    }
    for result in results:
        reason = f"{result.reason or ''} {result.quote or ''}".lower()
        condition = conditions.get(result.condition_id)
        if result.status == "INCONCLUSIVE" and any(marker in reason for marker in _NO_EVIDENCE_REASON_MARKERS):
            targets.add(result.condition_id)
        if result.status == "PROVEN" and any(marker in reason for marker in _ASSUMPTION_REASON_MARKERS):
            targets.add(result.condition_id)
        if result.status == "FAILED" and (
            result.execution_state == "NOT_EXECUTED"
            or any(marker in reason for marker in _NOT_EXECUTED_MARKERS)
        ):
            targets.add(result.condition_id)
        if result.status == "PENDING" and result.execution_state == "NOT_EXECUTED":
            targets.add(result.condition_id)
        if (
            result.status == "INCONCLUSIVE"
            and any(marker in evidence_text for marker in _HARD_VIOLATION_MARKERS)
        ):
            targets.add(result.condition_id)
        if condition and condition.requires_visual_evidence and visual_interpreted:
            if result.status == "UNTESTED":
                targets.add(result.condition_id)
            if result.status == "PROVEN" and result.subject_identity != "CONFIRMED":
                targets.add(result.condition_id)
            universal = bool(re.search(
                r"\b(?:all|every|each|any)\b",
                f"{contract.raw_text} {condition.description or ''}",
                re.IGNORECASE,
            ))
            if (
                result.status == "PROVEN"
                and universal
                and result.coverage_scope != "ALL_REQUIRED"
            ):
                targets.add(result.condition_id)

    same_page: dict[tuple[str, Any], list[str]] = {}
    for chunk in evidence_chunks:
        key = (str(chunk.get("document_id") or chunk.get("document_name") or ""), chunk.get("page_number"))
        same_page.setdefault(key, []).append(str(chunk.get("content") or chunk.get("quote") or ""))
    for passages in same_page.values():
        combined = "\n".join(passages).lower()
        checklist_failure = bool(re.search(r"(?:yes|no)\s*\(\s*fail\s*\)|question[^\n]*(?:fail|pass)", combined))
        explicit_result = bool(re.search(r"(?:result|measured|measurement|total|value)\s*[:=]?\s*-?\d", combined))
        if checklist_failure and explicit_result:
            targets.update(
                result.condition_id
                for result in results
                if result.status in {"PROVEN", "FAILED", "PENDING", "INCONCLUSIVE"}
            )

    declared = {condition.condition_id for condition in contract.atomic_conditions}
    return [
        condition.condition_id
        for condition in contract.atomic_conditions
        if condition.condition_id in targets & declared
    ]


def _focused_semantic_retry_prompt(
    base_prompt: str,
    results: list[ConditionVerificationResult],
    target_ids: list[str],
) -> str:
    previous = [
        result.model_dump(exclude_none=True)
        for result in results
        if result.condition_id in set(target_ids)
    ]
    return (
        base_prompt
        + "\n\nFOCUSED EVIDENCE-SEMANTICS REVIEW REQUIRED:\n"
        + f"- Recheck condition IDs: {', '.join(target_ids)}\n"
        + f"- Previous findings: {previous}\n"
        + "Reconcile excerpts from the same document and page as one structured form/table before deciding. "
          "A printed checklist question or its Pass/Fail option labels are not an observed answer unless the "
          "selected state is unambiguous; prefer the explicit measured/result field. If no supplied passage "
          "addresses a condition, use UNTESTED. Use INCONCLUSIVE only when relevant evidence addresses that "
          "condition but remains ambiguous, incomplete, method-incompatible, or inadmissible. A test explicitly "
          "recorded as not executed/not measured is UNTESTED, never FAILED. A qualified observed violation is "
          "FAILED, not INCONCLUSIVE, even when another excerpt reports an earlier PASS; use INCONCLUSIVE only "
          "when subject identity or applicability of that violation is genuinely uncertain. For visual evidence, "
          "set subject_identity and coverage_scope explicitly: one unidentified photograph cannot prove the "
          "controlled article or an every/all population claim. Never use implication, likelihood, or an unstated "
          "installation assumption as proof. Return every declared condition exactly once with exact evidence "
          "IDs and quotes. HARD OUTPUT CONSISTENCY CONSTRAINTS: execution_state=NOT_EXECUTED requires "
          "status=UNTESTED, evidence_value_role=NOT_ADDRESSED or REQUIRED_OR_PLANNED, and relationship="
          "NOT_ADDRESSED. It is invalid to return FAILED, VIOLATES, or OBSERVED for an unexecuted test or "
          "unmeasured endpoint. status=FAILED requires execution_state=EXECUTED plus an observed violating "
          "result. status=PENDING requires execution_state=PARTIALLY_EXECUTED (or EXECUTED when an observed "
          "subset was completed), relationship=PARTIAL_COVERAGE, and direct evidence of partial empirical "
          "coverage. Never use NOT_EXECUTED for PENDING. Apply these constraints even when the requirement "
          "expected the omitted endpoint."
    )


def _independent_secondary_model(primary_model: str) -> Optional[str]:
    """Choose a configured adjudicator that differs from the primary model."""
    configured = (settings.SECONDARY_ADJUDICATOR_MODEL or "").strip()
    if configured and configured.lower() != primary_model.lower():
        return configured
    return next((
        candidate
        for candidate in settings.DATABRICKS_FALLBACK_MODELS
        if candidate and candidate.lower() != primary_model.lower()
    ), None)


def _focused_evidence_catalog(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
) -> str:
    """Render the same isolated evidence excerpts with stable E identifiers."""
    blocks: list[str] = []
    for index, chunk in enumerate(evidence_chunks, 1):
        content = _isolate_relevant_passage(
            str(chunk.get("content") or chunk.get("quote") or "").strip(),
            contract,
            chunk.get("metadata"),
        )
        blocks.append(
            f"[E{index}] {chunk.get('document_name', 'Document')}"
            f" page={chunk.get('page_number') or 'unknown'}\n{content}"
        )
    return "\n\n".join(blocks) if blocks else "[No evidence supplied]"


def _secondary_adjudication_prompt(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    results: list[ConditionVerificationResult],
    target_ids: list[str],
) -> str:
    conditions = {
        condition.condition_id: condition
        for condition in contract.atomic_conditions
    }
    target_set = set(target_ids)
    condition_block = "\n".join(
        _condition_line(conditions[condition_id])
        for condition_id in target_ids
        if condition_id in conditions
    )
    previous = [
        result.model_dump(exclude_none=True)
        for result in results
        if result.condition_id in target_set
    ]
    return f"""You are the independent second reviewer for a safety-critical evidence audit.
The primary model already reviewed this requirement, then performed a focused retry, but the
conditions listed below remain internally inconsistent. Re-adjudicate ONLY those condition IDs.
Do not return results for any other condition and do not produce a requirement-level verdict.

Requirement ID: {contract.req_code}
Requirement clause: {contract.raw_text}
Logic: {_logic_line(contract)}
Conditions requiring independent adjudication:
{condition_block}

Primary model's unresolved findings:
{previous}

Evidence excerpts:
{_focused_evidence_catalog(contract, evidence_chunks)}

Binding label rules:
- FAILED requires execution_state=EXECUTED, evidence_value_role=OBSERVED, relationship=VIOLATES,
  and an explicit observed violating outcome.
- A test or endpoint explicitly not executed/not measured is UNTESTED with
  execution_state=NOT_EXECUTED and relationship=NOT_ADDRESSED. Missing execution is never FAILED.
- PENDING requires direct partial empirical coverage with relationship=PARTIAL_COVERAGE and
  execution_state=PARTIALLY_EXECUTED (or EXECUTED when a measured subset was completed).
  A merely planned, scheduled, or not-started test is UNTESTED, never PENDING.
- UNTESTED means no supplied evidence establishes an execution or outcome for that condition.
- INCONCLUSIVE means evidence directly addresses the condition but result, authority, identity,
  scope, method, or detail is insufficient to decide it.
- A rendered visual description is relevant evidence. If the controlled subject identity or a
  universal all/every scope is not established, use INCONCLUSIVE rather than UNTESTED or PROVEN.
- PROVEN/FAILED/PENDING must cite E identifiers and copy a literal supporting quote.
- Do not preserve the first model's label merely for agreement. Decide from the supplied evidence.
Return a SemanticAdjudicationResult containing exactly one result for each requested condition.
"""


async def _apply_secondary_adjudication(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    results: list[ConditionVerificationResult],
    primary_model: str,
) -> tuple[list[ConditionVerificationResult], dict[str, Any]]:
    """Ask a different LLM to resolve only still-inconsistent conditions.

    Python detects contradictions and validates the shape of the response, but
    never invents or rewrites a semantic condition label itself.
    """
    target_ids = _semantic_retry_condition_ids(contract, results, evidence_chunks)
    diagnostics: dict[str, Any] = {
        "secondary_adjudication_condition_ids": target_ids,
        "secondary_adjudication_resolved_ids": [],
        "secondary_adjudication_unresolved_ids": list(target_ids),
    }
    if not target_ids or not settings.SECONDARY_ADJUDICATOR_ENABLED:
        return results, diagnostics

    secondary_model = _independent_secondary_model(primary_model)
    diagnostics["secondary_adjudicator_model"] = secondary_model
    if not secondary_model:
        logger.warning(
            "No independent secondary adjudicator is configured for %s.",
            contract.req_code,
        )
        return results, diagnostics

    try:
        adjudication = await generate_structured(
            prompt=_secondary_adjudication_prompt(
                contract,
                evidence_chunks,
                results,
                target_ids,
            ),
            response_model=SemanticAdjudicationResult,
            model=secondary_model,
            system_instruction=(
                "You are an independent compliance adjudicator. Resolve only the requested "
                "condition semantics from explicit evidence and obey the status taxonomy exactly."
            ),
            thinking_level=settings.GEMINI_THINKING_LEVEL,
            max_output_tokens=4096,
            allow_model_fallback=False,
        )
    except Exception as error:
        logger.warning(
            "Secondary semantic adjudication failed for %s: %s",
            contract.req_code,
            error,
        )
        return results, diagnostics

    if not adjudication:
        return results, diagnostics

    canonical, _ = _canonical_condition_results(contract, adjudication.condition_results)
    candidates = {
        item.condition_id: item
        for item in canonical
        if item.condition_id in set(target_ids)
    }
    replacements: dict[str, ConditionVerificationResult] = {}
    resolved: list[str] = []
    for condition_id in target_ids:
        candidate = candidates.get(condition_id)
        if candidate is None:
            continue
        remains_inconsistent = condition_id in _semantic_retry_condition_ids(
            contract,
            [candidate],
            evidence_chunks,
        )
        if remains_inconsistent:
            continue
        replacements[condition_id] = candidate
        resolved.append(condition_id)

    diagnostics["secondary_adjudication_resolved_ids"] = resolved
    diagnostics["secondary_adjudication_unresolved_ids"] = [
        condition_id for condition_id in target_ids if condition_id not in set(resolved)
    ]
    if not replacements:
        return results, diagnostics
    return [
        replacements.get(item.condition_id, item)
        for item in results
    ], diagnostics


def _evidence_provenance(
    evidence_chunks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        f"E{index}": {
            "document_name": chunk.get("document_name"),
            "page_number": chunk.get("page_number"),
        }
        for index, chunk in enumerate(evidence_chunks, 1)
    }


def _attach_literal_citation_spans(
    result: ConditionVerificationResult,
    evidence_contents: dict[str, str],
    provenance: dict[str, dict[str, Any]],
) -> bool:
    """Attach offsets when the current quote is already a literal source span."""
    if not result.quote:
        return False
    normalized_refs = []
    for evidence_id in result.evidence_ids:
        match = re.search(r"(\d+)", str(evidence_id))
        if match:
            normalized_refs.append(f"E{match.group(1)}")
    search_order = list(dict.fromkeys(normalized_refs + list(evidence_contents)))
    for evidence_id in search_order:
        content = evidence_contents.get(evidence_id, "")
        offsets = locate_exact_quote_span(result.quote, content)
        if not offsets:
            continue
        start, end = offsets
        source = provenance.get(evidence_id, {})
        result.evidence_ids = [evidence_id]
        result.quote = content[start:end]
        result.evidence_spans = [EvidenceSpanReference(
            evidence_id=evidence_id,
            exact_quote=result.quote,
            start_offset=start,
            end_offset=end,
            document_name=source.get("document_name"),
            page_number=source.get("page_number"),
        )]
        return True
    return False


def _citation_grounding_prompt(
    contract: RequirementContract,
    results: list[ConditionVerificationResult],
    target_ids: list[str],
    evidence_contents: dict[str, str],
    provenance: dict[str, dict[str, Any]],
) -> str:
    conditions = {item.condition_id: item for item in contract.atomic_conditions}
    targets = []
    for result in results:
        if result.condition_id not in set(target_ids):
            continue
        targets.append({
            "condition_id": result.condition_id,
            "condition": conditions.get(result.condition_id).description
            if conditions.get(result.condition_id) else result.description,
            "status": result.status,
            "reason": result.reason,
            "current_evidence_ids": result.evidence_ids,
            "current_quote": result.quote,
        })
    evidence = "\n\n".join(
        f"[{evidence_id}] {provenance.get(evidence_id, {}).get('document_name') or 'Document'} "
        f"page={provenance.get(evidence_id, {}).get('page_number') or 'unknown'}\n{content}"
        for evidence_id, content in evidence_contents.items()
    )
    return f"""Ground citations for an existing compliance decision. You MUST NOT change, reassess,
or comment on any condition status. For each target, locate the smallest complete source passage
that directly supports the existing status and copy it literally from exactly one supplied evidence
excerpt. Preserve every character, symbol, capitalization, and line break. Do not paraphrase,
summarize, repair OCR, combine non-contiguous text, or invent a quote. Prefer enough surrounding
header/row context to make a table value meaningful. If no literal passage directly supports the
existing status, omit that condition from `citations`.

Requirement: {contract.req_code} — {contract.raw_text}
Targets: {targets}

Evidence excerpts:
{evidence}
"""


async def _ground_condition_citations(
    contract: RequirementContract,
    evidence_chunks: list[dict[str, Any]],
    evidence_contents: dict[str, str],
    results: list[ConditionVerificationResult],
    primary_model: str,
) -> tuple[list[ConditionVerificationResult], dict[str, Any]]:
    """Produce verified extractive spans without altering semantic statuses."""
    provenance = _evidence_provenance(evidence_chunks)
    attributed = {"PROVEN", "FAILED", "PENDING"}
    targets: list[str] = []
    grounded: list[str] = []
    for result in results:
        if result.status not in attributed:
            continue
        if _attach_literal_citation_spans(result, evidence_contents, provenance):
            grounded.append(result.condition_id)
        else:
            targets.append(result.condition_id)

    diagnostics: dict[str, Any] = {
        "citation_grounding_requested_ids": targets,
        "citation_grounded_ids": list(grounded),
        "citation_grounding_failed_ids": list(targets),
    }
    if not targets or not settings.CITATION_GROUNDING_ENABLED:
        return results, diagnostics

    grounding_model = (settings.CITATION_GROUNDING_MODEL or "").strip()
    if not grounding_model:
        grounding_model = _independent_secondary_model(primary_model) or primary_model
    diagnostics["citation_grounding_model"] = grounding_model
    try:
        response = await generate_structured(
            prompt=_citation_grounding_prompt(
                contract,
                results,
                targets,
                evidence_contents,
                provenance,
            ),
            response_model=CitationGroundingResult,
            model=grounding_model,
            system_instruction=(
                "You are an extractive citation locator. Copy literal contiguous source spans only; "
                "never change the supplied semantic decisions."
            ),
            thinking_level=settings.GEMINI_THINKING_LEVEL,
            max_output_tokens=3072,
        )
    except Exception as error:
        logger.warning("Citation grounding failed for %s: %s", contract.req_code, error)
        response = None

    by_id = {item.condition_id: item for item in results}
    accepted: set[str] = set()
    if response:
        for candidate in response.citations:
            if candidate.condition_id not in set(targets) or candidate.condition_id in accepted:
                continue
            evidence_match = re.search(r"(\d+)", candidate.evidence_id or "")
            if not evidence_match:
                continue
            evidence_id = f"E{evidence_match.group(1)}"
            content = evidence_contents.get(evidence_id, "")
            offsets = locate_exact_quote_span(candidate.exact_quote, content)
            # Reject token-sized labels such as a bare "PASS"; grounding must
            # retain enough source context to be independently auditable.
            if not offsets or len(_normalized_grounding_tokens(candidate.exact_quote)) < 4:
                continue
            result = by_id[candidate.condition_id]
            start, end = offsets
            source = provenance.get(evidence_id, {})
            result.evidence_ids = [evidence_id]
            result.quote = content[start:end]
            result.evidence_spans = [EvidenceSpanReference(
                evidence_id=evidence_id,
                exact_quote=result.quote,
                start_offset=start,
                end_offset=end,
                document_name=source.get("document_name"),
                page_number=source.get("page_number"),
            )]
            accepted.add(candidate.condition_id)

    grounded.extend(condition_id for condition_id in targets if condition_id in accepted)
    diagnostics["citation_grounded_ids"] = list(dict.fromkeys(grounded))
    diagnostics["citation_grounding_failed_ids"] = [
        condition_id for condition_id in targets if condition_id not in accepted
    ]
    return results, diagnostics


def _normalized_grounding_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9µ%]+", (value or "").lower().replace("μ", "µ"))


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
    """Audit structured model facts and enforce output-schema invariants.

    The LLM owns evidence interpretation. Python records inconsistencies as
    validation metadata and normalizes only logically impossible combinations
    of fields supplied by the model itself. This is schema reconciliation, not
    an independent evidence verdict engine.
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

        # NOT_EXECUTED states that no test/measurement outcome exists. Partial
        # work has its own PARTIALLY_EXECUTED state, so NOT_EXECUTED remains
        # incompatible with PROVEN, FAILED, or PENDING. Normalize the
        # status from the model's own execution metadata and keep the rewrite in
        # condition_transitions for a fully auditable trace.
        if (
            result.execution_state == "NOT_EXECUTED"
            and result.status not in ("UNTESTED", "NOT_APPLICABLE")
        ):
            previous_status = result.status
            result.status = "UNTESTED"
            result.relationship = "NOT_ADDRESSED"
            if result.evidence_value_role in ("OBSERVED", "UNCLEAR"):
                result.evidence_value_role = "NOT_ADDRESSED"
            result.validation_state = "VALID"
            result.validation_notes.append(
                f"Normalized impossible {previous_status}/NOT_EXECUTED combination to UNTESTED; "
                "no executed observation exists to prove or fail the condition."
            )

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

        if result.status == "PENDING" and result.execution_state not in (
            "PARTIALLY_EXECUTED",
            "EXECUTED",
        ):
            result.validation_state = "CONTRADICTED"
            result.validation_notes.append(
                "PENDING lacks a partially executed or executed empirical result; "
                "the semantic status was preserved and must remain under review."
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

        if result.status == "FAILED" and (
            result.execution_state != "EXECUTED"
            or result.evidence_value_role != "OBSERVED"
            or result.relationship != "VIOLATES"
        ):
            result.validation_state = "CONTRADICTED"
            result.validation_notes.append(
                "FAILED lacks a consistent executed, observed, violating result after LLM adjudication; "
                "the semantic status was preserved and must remain under review."
            )

        if (
            result.status == "PROVEN"
            and condition.requires_visual_evidence
            and result.subject_identity != "CONFIRMED"
        ):
            result.validation_state = "CONTRADICTED"
            result.validation_notes.append(
                "Visual proof does not confirm the controlled subject identity; the semantic status was preserved."
            )
        if (
            result.status == "PROVEN"
            and condition.requires_visual_evidence
            and re.search(
                r"\b(?:all|every|each|any)\b",
                f"{contract.raw_text} {condition.description or ''}",
                re.IGNORECASE,
            )
            and result.coverage_scope != "ALL_REQUIRED"
        ):
            result.validation_state = "CONTRADICTED"
            result.validation_notes.append(
                "Visual proof does not establish the complete required population scope; "
                "the semantic status was preserved."
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
            (c.get("content") or c.get("quote") or "").strip(),
            contract,
            c.get("metadata"),
        )
        anno = format_qualification_annotation(q)
        metadata = c.get("metadata") or {}
        structure = str(metadata.get("block_type") or "text")
        context_note = "; structural context" if metadata.get("context_only") else ""
        formatted_evidence.append(
            f"--- [Evidence Excerpt #{i} ({q.evidence_id}): {doc_name}{page_str}; "
            f"block={structure}{context_note}] ---\n{anno}\n{content}\n"
        )

    evidence_block = "\n".join(formatted_evidence) if formatted_evidence else "[No evidence retrieved]"

    cond_descriptions = []
    if contract.atomic_conditions:
        for c in contract.atomic_conditions:
            cond_descriptions.append(_condition_line(c))
    else:
        cond_descriptions.append(f"  - Primary Clause: {contract.title}")

    prompt = f"""Requirement to Verify:
- Requirement ID: {contract.req_code}
- Title: {contract.title}
- Category: {contract.category}
- Scope / Entity: {contract.scope or 'System'}
- Required Verification Method: {contract.verification_method or 'physical_test'}
- Specification Clause: {contract.raw_text}
- Regulatory Logic: {_logic_line(contract)}
- Atomic Conditions:
{chr(10).join(cond_descriptions)}

Retrieved Technical Evidence:
{evidence_block}

Auditing Protocol & Verification Rules:

STEP 1: EVIDENCE ATTRIBUTION & RELEVANCE CHECK (Filter Similarity Noise)
- For each retrieved excerpt, evaluate whether it provides DIRECT verification evidence for the target requirement and its specified entity/subsystem, or if it was fetched merely due to keyword/vector similarity.
- Excerpts from the same document and page are parts of one structural evidence group. Reconcile table headers, rows, checkboxes, captions, footnotes, and neighboring text together before interpreting a value or verdict.
- A printed checklist question and its possible Pass/Fail choices are not an observed result unless the selected state is unambiguous. Prefer an explicit measured value or completed result field; if the structure remains ambiguous, return INCONCLUSIVE instead of choosing an option.
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
   - Return every declared condition exactly once in `condition_results`, using its exact condition ID. Never omit a condition.
   - Use exactly one state: PROVEN (qualified evidence establishes it), FAILED (qualified/relevant hard evidence contradicts it), PENDING (qualified empirical work directly covers only part of it), UNTESTED (no relevant verification evidence exists), NOT_APPLICABLE (an alternative path or conditional branch is genuinely inapplicable), or INCONCLUSIVE (relevant evidence exists but has insufficient authority, method, scope, parameter alignment, or detail).
   - JUDGE EVERY CONDITION INDEPENDENTLY. A sibling condition's missing range, pending test, or failure changes the final requirement status but must never downgrade an independently satisfied condition.
   - PENDING describes partial empirical coverage of THIS condition only. Never use PENDING merely because the overall requirement is PARTIAL. If this condition is fully satisfied by qualified evidence, return PROVEN even when another condition remains incomplete.
   - For every condition, also return the exact observed parameter/value/unit, `evidence_value_role` (OBSERVED, REQUIRED_OR_PLANNED, STATUS_ONLY, NOT_ADDRESSED, or UNCLEAR), and `relationship` (SATISFIES, VIOLATES, PARTIAL_COVERAGE, NOT_ADDRESSED, or UNCLEAR).
   - Also return `execution_state` (EXECUTED, PARTIALLY_EXECUTED, NOT_EXECUTED, NOT_ADDRESSED, UNKNOWN), `subject_identity` (CONFIRMED, UNCONFIRMED, NOT_REQUIRED, UNKNOWN), and `coverage_scope` (ALL_REQUIRED, SAMPLE, SINGLE_ITEM, NOT_APPLICABLE, UNKNOWN) for every condition.
   - A required, target, planned, scheduled, pending, or not-yet-tested value is NOT an observation. Never use it as measured proof. Set `evidence_value_role` to REQUIRED_OR_PLANNED.
   - FAILED requires an executed observation that violates the condition. If the report says the test, endpoint, or measurement was not executed, set execution_state=NOT_EXECUTED and status=UNTESTED; absence of a test is not a failed test.
   - PENDING requires direct evidence that this condition was partly performed or empirically covered. Set execution_state=PARTIALLY_EXECUTED (or EXECUTED when a measured subset was completed), relationship=PARTIAL_COVERAGE, and evidence_value_role=OBSERVED or STATUS_ONLY. A merely planned, scheduled, or not-started test is UNTESTED, not PENDING.
   - Use `observed_min_value` and `observed_max_value` for an observed range. Use `observed_value` for a scalar, boolean, or categorical observation. Copy the observed unit exactly.
   - Apply the stated Regulatory Logic. ALL_OF requires every applicable condition. ANY_OF is satisfied when at least one alternative path is PROVEN; mark genuinely unused alternatives NOT_APPLICABLE. For IF_THEN, first decide the antecedent and evaluate every consequent when it applies.
   - A figure caption or image placeholder without an actual visual description cannot prove a visual condition. Return INCONCLUSIVE, not UNTESTED, because relevant visual evidence exists but has not been interpreted.
   - A close-up/example image of a label can establish only the visible appearance shown. It cannot prove that the marking is installed on every required device, barrier, or location unless the image or accompanying text explicitly establishes that scope.
   - Visual proof is PROVEN only when the depicted subject is linked to the controlled/tested article (subject_identity=CONFIRMED). If identity is missing, use INCONCLUSIVE. For words such as every/all/each, PROVEN additionally requires coverage_scope=ALL_REQUIRED; a single image or sample is INCONCLUSIVE.
   - UNTESTED means no supplied evidence addresses execution or outcome for THIS condition, even when the same report proves sibling conditions. INCONCLUSIVE means an excerpt directly addresses THIS condition but its result, authority, method, scope, or detail cannot establish a conclusion.
   - Never mark PROVEN because evidence merely implies, suggests, likely satisfies, or is assumed to satisfy a condition. If the required fact is not explicit or directly observable, use UNTESTED or INCONCLUSIVE as defined above.
   - Derive relational conditions from observed table operands. If a table gives A and B, compare them directly; do not require a separate sentence spelling out A >= B. PDF labels may separate subscripts or primes (for example `V = 1` means V1 and `V ’ = 1` means V1-prime).
   - Every PROVEN, FAILED, or PENDING condition MUST include at least one evidence ID (E1, E2, ...) and a verbatim quote from that evidence. Never return an attributed condition status without both fields.
   - A qualified local failure, violation, leakage, exceeded limit, or lower achieved rating dominates an earlier PASS word across the supplied evidence set when both concern the same subject and condition. Do not average a demonstrated violation into INCONCLUSIVE.

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
                (c.get("content") or c.get("quote") or "").strip(),
                contract,
                c.get("metadata"),
            )
            anno = format_qualification_annotation(q)
            metadata = c.get("metadata") or {}
            structure = str(metadata.get("block_type") or "text")
            context_note = "; structural context" if metadata.get("context_only") else ""
            formatted_evidence.append(
                f"  [Excerpt #{j} ({q.evidence_id}): {doc_name}{page_str}; "
                f"block={structure}{context_note}]\n  {anno}\n  {content}"
            )
        evidence_str = "\n".join(formatted_evidence) if formatted_evidence else "  [No independent evidence retrieved]"

        cond_lines = []
        if contract.atomic_conditions:
            for c in contract.atomic_conditions:
                cond_lines.append(_condition_line(c))
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
            f"- Regulatory Logic: {_logic_line(contract)}\n"
            f"- Atomic Conditions to Verify:\n{chr(10).join(cond_lines)}\n"
            f"- Retrieved Technical Evidence:\n{evidence_str}\n"
        )

    prompt = f"""Evaluate the following batch of {len(batch_items)} engineering requirements against their respective retrieved technical evidence excerpts:

{"\n".join(req_blocks)}

Auditing Protocol & Verification Rules for each requirement:

STEP 1: EVIDENCE ATTRIBUTION & RELEVANCE CHECK (Filter Similarity Noise)
- For each requirement, evaluate whether retrieved excerpts provide DIRECT verification evidence for the target requirement and its specified entity/subsystem, or if fetched merely due to keyword/vector similarity.
- Treat excerpts from the same document and page as one structural evidence group. Reconcile headers, rows, checkboxes, captions, footnotes, and neighboring text before deciding.
- Printed checklist questions and their possible Pass/Fail option labels are not observed answers unless selection is unambiguous. Prefer explicit measured/result fields; unresolved structure is INCONCLUSIVE.
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
   - For each requirement item, return every defined condition exactly once using its exact ID and one state: PROVEN, FAILED, PENDING, UNTESTED, NOT_APPLICABLE, or INCONCLUSIVE.
   - JUDGE EVERY CONDITION INDEPENDENTLY. A sibling condition's missing range, pending test, or failure changes the final requirement status but must never downgrade an independently satisfied condition.
   - PENDING describes partial empirical coverage of THIS condition only. Never use PENDING merely because the overall requirement is PARTIAL. If this condition is fully satisfied by qualified evidence, return PROVEN even when another condition remains incomplete.
   - For every condition, also return the exact observed parameter/value/unit, `evidence_value_role` (OBSERVED, REQUIRED_OR_PLANNED, STATUS_ONLY, NOT_ADDRESSED, or UNCLEAR), and `relationship` (SATISFIES, VIOLATES, PARTIAL_COVERAGE, NOT_ADDRESSED, or UNCLEAR).
   - Also return `execution_state` (EXECUTED, PARTIALLY_EXECUTED, NOT_EXECUTED, NOT_ADDRESSED, UNKNOWN), `subject_identity` (CONFIRMED, UNCONFIRMED, NOT_REQUIRED, UNKNOWN), and `coverage_scope` (ALL_REQUIRED, SAMPLE, SINGLE_ITEM, NOT_APPLICABLE, UNKNOWN) for every condition.
   - A required, target, planned, scheduled, pending, or not-yet-tested value is NOT an observation. Never use it as measured proof. Set `evidence_value_role` to REQUIRED_OR_PLANNED.
   - FAILED requires an executed observation that violates the condition. If a test, endpoint, or measurement was not executed, set execution_state=NOT_EXECUTED and status=UNTESTED; absence of a test is not a failed test.
   - PENDING requires direct evidence that this condition was partly performed or empirically covered. Set execution_state=PARTIALLY_EXECUTED (or EXECUTED when a measured subset was completed), relationship=PARTIAL_COVERAGE, and evidence_value_role=OBSERVED or STATUS_ONLY. A merely planned, scheduled, or not-started test is UNTESTED, not PENDING.
   - Use `observed_min_value` and `observed_max_value` for an observed range. Use `observed_value` for a scalar, boolean, or categorical observation. Copy the observed unit exactly.
   - Apply each item's explicit Regulatory Logic: ALL_OF, ANY_OF, or IF_THEN. Do not treat alternative branches as mandatory siblings.
   - A figure caption or image placeholder without an actual visual description makes a visual condition INCONCLUSIVE, not UNTESTED or PROVEN.
   - A close-up/example label image proves only the appearance shown, not installation on every required device, barrier, or location unless that scope is explicit.
   - Visual proof requires subject_identity=CONFIRMED. Missing controlled-article identity is INCONCLUSIVE. Universal every/all/each claims additionally require coverage_scope=ALL_REQUIRED; one image or a sample is INCONCLUSIVE.
   - UNTESTED means no supplied evidence addresses THIS condition. Do not call a condition INCONCLUSIVE merely because the report addresses a sibling condition. Use INCONCLUSIVE only when evidence directly addresses this condition but remains ambiguous or inadmissible.
   - Never mark PROVEN from implication, likelihood, or an unstated installation assumption. Use UNTESTED or INCONCLUSIVE according to the preceding definitions.
   - Derive comparisons from observed table operands even when no prose conclusion is printed. PDF extraction may separate subscripts and primes, such as `V = 1` for V1 and `V ’ = 1` for V1-prime.
   - Every PROVEN, FAILED, or PENDING condition MUST include at least one evidence ID (E1, E2, ...) and a verbatim quote from that evidence. Never return an attributed condition status without both fields.
   - A qualified local failure, violation, leakage, exceeded limit, or lower achieved rating dominates an earlier PASS across the supplied evidence set when both concern the same subject and condition. Do not average a demonstrated violation into INCONCLUSIVE.

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
            max_output_tokens=8192,
        )
        if batch_resp and batch_resp.batch_results:
            item_by_code = {it["contract"].req_code: it for it in batch_items}
            for item_res in batch_resp.batch_results:
                it = item_by_code.get(item_res.req_code)
                if it:
                    contract: RequirementContract = it["contract"]
                    canonical, missing = _canonical_condition_results(contract, item_res.condition_results)
                    if missing:
                        logger.warning(
                            "Batch result for %s omitted condition(s) %s; retrying individually.",
                            contract.req_code,
                            ", ".join(missing),
                        )
                        continue
                    item_res.condition_results = canonical
                    logic_retry_ids = _logic_retry_condition_ids(contract, canonical)
                    if logic_retry_ids:
                        logger.info(
                            "Batch result for %s has unresolved logic gate(s) %s; retrying individually.",
                            contract.req_code,
                            ", ".join(logic_retry_ids),
                        )
                        continue
                    cand_chunks: list[dict] = it.get("candidate_chunks", [])
                    semantic_retry_ids = _semantic_retry_condition_ids(contract, canonical, cand_chunks)
                    if semantic_retry_ids:
                        logger.info(
                            "Batch result for %s needs focused semantic review of %s; retrying individually.",
                            contract.req_code,
                            ", ".join(semantic_retry_ids),
                        )
                        continue
                    quals = qualify_evidence_chunks(contract, cand_chunks)
                    qual_contents = {
                        q.evidence_id: _isolate_relevant_passage(
                            (c.get("content") or c.get("quote") or ""),
                            contract,
                            c.get("metadata"),
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
                    provisional._diagnostics = {
                        "decision_source": "llm",
                        "llm_provisional_status": provisional.status,
                        "llm_provisional_confidence": provisional.confidence,
                    }
                    provisional.condition_results, citation_diagnostics = (
                        await _ground_condition_citations(
                            contract,
                            cand_chunks,
                            qual_contents,
                            provisional.condition_results,
                            active_model,
                        )
                    )
                    provisional._diagnostics.update(citation_diagnostics)
                    original_conditions = _snapshot_condition_results(provisional.condition_results)
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
                spec_doc_names=item.get("spec_doc_names"),
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
            max_output_tokens=8192,
        )
        if result and result.status in ("SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT"):
            logic_retry_ids: list[str] = []
            semantic_retry_ids: list[str] = []
            canonical, missing = _canonical_condition_results(contract, result.condition_results)
            result.condition_results = canonical
            if missing:
                retry_prompt = (
                    prompt
                    + "\n\nCORRECTION REQUIRED: The previous response omitted these condition IDs: "
                    + ", ".join(missing)
                    + ". Re-evaluate the requirement and return EVERY declared condition exactly once."
                )
                try:
                    retry = await generate_structured(
                        prompt=retry_prompt,
                        response_model=VerificationAnalysisResult,
                        model=active_model,
                        system_instruction=system_instruction,
                        thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
                        max_output_tokens=8192,
                    )
                except Exception as retry_error:
                    logger.warning(
                        "Focused condition retry failed for %s: %s",
                        contract.req_code,
                        retry_error,
                    )
                    retry = None
                if retry:
                    retry_canonical, _ = _canonical_condition_results(contract, retry.condition_results)
                    by_id = {item.condition_id: item for item in canonical}
                    by_id.update({item.condition_id: item for item in retry_canonical})
                    result.condition_results = list(by_id.values())
                    result.status = retry.status
                    result.confidence = retry.confidence
                    result.reason = retry.reason
                    result.highlight = retry.highlight
            logic_retry_ids = _logic_retry_condition_ids(contract, result.condition_results)
            if logic_retry_ids:
                logic_prompt = _focused_logic_retry_prompt(
                    prompt,
                    contract,
                    result.condition_results,
                    logic_retry_ids,
                )
                try:
                    logic_retry = await generate_structured(
                        prompt=logic_prompt,
                        response_model=VerificationAnalysisResult,
                        model=active_model,
                        system_instruction=system_instruction,
                        thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
                        max_output_tokens=8192,
                    )
                except Exception as retry_error:
                    logger.warning(
                        "Focused logic retry failed for %s: %s",
                        contract.req_code,
                        retry_error,
                    )
                    logic_retry = None
                if logic_retry:
                    retry_canonical, _ = _canonical_condition_results(
                        contract,
                        logic_retry.condition_results,
                    )
                    replacement = {
                        item.condition_id: item
                        for item in retry_canonical
                        if item.condition_id in set(logic_retry_ids)
                    }
                    result.condition_results = [
                        replacement.get(item.condition_id, item)
                        for item in result.condition_results
                    ]
                    result.status = logic_retry.status
                    result.confidence = logic_retry.confidence
                    result.reason = logic_retry.reason
                    result.highlight = logic_retry.highlight
            semantic_retry_ids = _semantic_retry_condition_ids(
                contract,
                result.condition_results,
                evidence_chunks,
            )
            if semantic_retry_ids:
                semantic_prompt = _focused_semantic_retry_prompt(
                    prompt,
                    result.condition_results,
                    semantic_retry_ids,
                )
                try:
                    semantic_retry = await generate_structured(
                        prompt=semantic_prompt,
                        response_model=VerificationAnalysisResult,
                        model=active_model,
                        system_instruction=system_instruction,
                        thinking_level=thinking_level or settings.GEMINI_THINKING_LEVEL,
                        max_output_tokens=8192,
                    )
                except Exception as retry_error:
                    logger.warning(
                        "Focused evidence-semantics retry failed for %s: %s",
                        contract.req_code,
                        retry_error,
                    )
                    semantic_retry = None
                if semantic_retry:
                    retry_canonical, _ = _canonical_condition_results(
                        contract,
                        semantic_retry.condition_results,
                    )
                    replacement = {
                        item.condition_id: item
                        for item in retry_canonical
                        if item.condition_id in set(semantic_retry_ids)
                    }
                    result.condition_results = [
                        replacement.get(item.condition_id, item)
                        for item in result.condition_results
                    ]
                    result.status = semantic_retry.status
                    result.confidence = semantic_retry.confidence
                    result.reason = semantic_retry.reason
                    result.highlight = semantic_retry.highlight

            primary_conditions_after_retry = _snapshot_condition_results(result.condition_results)
            result.condition_results, secondary_diagnostics = await _apply_secondary_adjudication(
                contract,
                evidence_chunks,
                result.condition_results,
                active_model,
            )
            result.condition_results = _fill_missing_conditions(contract, result.condition_results)
            result._diagnostics = {
                "decision_source": "llm",
                "llm_provisional_status": result.status,
                "llm_provisional_confidence": result.confidence,
                "logic_retry_condition_ids": logic_retry_ids,
                "semantic_retry_condition_ids": semantic_retry_ids,
                "primary_condition_results_after_retry": primary_conditions_after_retry,
                **secondary_diagnostics,
            }
            quals = qualify_evidence_chunks(contract, evidence_chunks, spec_doc_names=spec_doc_names)
            qual_contents = {
                q.evidence_id: _isolate_relevant_passage(
                    (c.get("content") or c.get("quote") or ""),
                    contract,
                    c.get("metadata"),
                )
                for q, c in zip(quals, evidence_chunks)
            }
            result.condition_results, citation_diagnostics = await _ground_condition_citations(
                contract,
                evidence_chunks,
                qual_contents,
                result.condition_results,
                active_model,
            )
            result._diagnostics.update(citation_diagnostics)
            original_conditions = _snapshot_condition_results(result.condition_results)
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
