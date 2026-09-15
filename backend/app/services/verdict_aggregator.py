"""Python-owned final verdict aggregation.

Architecture principle:
    LLM  = whole-clause interpretation plus condition-level findings
    Python = deterministic validation and aggregation when decomposition is reliable

Every verification path computes an atomic aggregate. Contract incompleteness,
unmapped obligations, ambiguity and low decomposition confidence are advisory:
they force review but do not prevent a final status for the extracted conditions.
Malformed Boolean structure, ambiguous alignment and invalid decisive evidence
make the aggregator abstain; the provisional verdict is then retained with
reduced confidence and mandatory-review diagnostics.

Precedence for mandatory conditions:
    1. Any mandatory condition FAILED                     -> CONFLICT
    2. All mandatory conditions PROVEN                    -> SUPPORTED
    3. Some PROVEN + some PENDING/UNTESTED/INCONCLUSIVE   -> PARTIAL
    3b. Any PENDING (partial coverage / in progress)      -> PARTIAL
    4. Authoritative record states evidence absent        -> MISSING
    5. Relevant evidence exists, nothing established      -> UNKNOWN
    6. No meaningful evidence                              -> MISSING

Condition status semantics (shared by LLM and deterministic paths):
    PROVEN       — condition established by QUALIFIED evidence
    FAILED       — condition violated by qualified evidence, or refuted by a
                   scope/parameter-relevant hard rating (datasheet limit)
    PENDING      — verification partially covers the condition (narrower
                   tested range, in-progress work) but does not reach it
    UNTESTED     — no qualified evidence addresses the condition
    INCONCLUSIVE — claimed conclusion was rejected by the qualification layer
                   (wrong method/scope/parameter) — never counts as proof
"""

import re
import logging
from typing import Any, Optional

from app.schemas.contract import RequirementContract, AtomicConditionContract
from app.schemas.contract_logic import contract_tree
from app.schemas.predicate import predicate_issues
from app.schemas.validation_issue import ValidationIssue
from app.schemas.claim import EvidenceClaim
from app.services.taxonomy import condition_to_verdict
from app.schemas.verification_result import (
    ConditionVerificationResult,
    VerificationAnalysisResult,
)
from app.schemas.evidence_qualification import (
    EvidenceQualification,
    extract_parameters_from_text,
    normalize_parameter,
    parameters_compatible,
)
from app.services.evidence_qualification import condition_evidence_compatible
from app.services.units import convert_value, are_units_compatible

logger = logging.getLogger("traceaudit.aggregator")

HARD_RATING_AUTHORITIES = ("DATASHEET", "ARCHITECTURE_SPEC")


def _condition_snapshot(
    condition_results: list[ConditionVerificationResult],
) -> list[dict[str, Any]]:
    """Serialize condition decisions at one pipeline boundary."""
    return [result.model_dump(exclude_none=True) for result in condition_results]


def _condition_transitions(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    stage: str,
) -> list[dict[str, Any]]:
    """Describe condition-status rewrites without guessing their correctness."""
    after_by_id = {item.get("condition_id"): item for item in after}
    transitions: list[dict[str, Any]] = []
    for previous in before:
        condition_id = previous.get("condition_id")
        current = after_by_id.get(condition_id)
        if current is None or previous.get("status") == current.get("status"):
            continue
        transitions.append({
            "stage": stage,
            "condition_id": condition_id,
            "from_status": previous.get("status"),
            "to_status": current.get("status"),
            "reason": current.get("reason"),
        })
    return transitions


# ── Mandatory condition resolution ──────────────────────────────────────────

def mandatory_conditions(contract: Any) -> list[AtomicConditionContract]:
    """The conditions that must ALL be proven for SUPPORTED.

    Falls back to a single synthesized primary clause when the contract
    parser produced no atomic decomposition.
    """
    if isinstance(contract, list):
        return [c for c in contract if getattr(c, "mandatory", True)]
    if not hasattr(contract, "atomic_conditions"):
        return []
    mandatory = [c for c in contract.atomic_conditions if c.mandatory]
    if mandatory:
        return mandatory
    req_code = getattr(contract, "req_code", "REQ")
    title = getattr(contract, "title", "Primary Clause")
    return [AtomicConditionContract(
        condition_id=f"{req_code}-C1",
        description=title,
        parameter=getattr(contract, "parameter", None),
        operator=getattr(contract, "operator", None),
        threshold=getattr(contract, "expected_value", None),
        min_value=getattr(contract, "min_value", None),
        max_value=getattr(contract, "max_value", None),
        unit=getattr(contract, "unit", None),
        scope=getattr(contract, "scope", None),
        verification_method=getattr(contract, "verification_method", None),
    )]


def _match_condition_results(
    contract: Any,
    condition_results: list[ConditionVerificationResult],
) -> list[ConditionVerificationResult]:
    """Align LLM condition results onto the contract's mandatory conditions.

    Matches by condition_id first (exact, then suffix match: LLMs often
    shorten 'REQ-123-C2' to 'C2'). Unmatched conditions become UNTESTED.
    When the contract has no atomic decomposition, the returned results are
    used as-is (the producer performed the decomposition).
    """
    if isinstance(contract, list):
        mand = list(contract)
    elif hasattr(contract, "atomic_conditions"):
        logic = getattr(contract, "logic", None)
        declared_ids = set(getattr(logic, "condition_ids", []) or [])
        if getattr(contract, "logic_tree", None) is not None:
            declared_ids = set(_logic_tree_condition_ids(contract.logic_tree))
        if declared_ids:
            mand = [c for c in contract.atomic_conditions if c.condition_id in declared_ids]
        else:
            mand = [c for c in contract.atomic_conditions if c.mandatory]
    else:
        mand = []

    if not mand:
        return list(condition_results) or [
            ConditionVerificationResult(condition_id=f"{contract.req_code}-C1", status="UNTESTED")
        ]

    by_exact = {cr.condition_id: cr for cr in condition_results if cr.condition_id}
    aligned: list[ConditionVerificationResult] = []
    for idx, cond in enumerate(mand):
        cr = by_exact.get(cond.condition_id)
        if cr is None:
            # suffix match: "C2" or "001-C2" against "...-C2"
            suffix = cond.condition_id.rsplit("-", 1)[-1].lower()
            for candidate in condition_results:
                cid = (candidate.condition_id or "").lower()
                if cid == suffix or cid.endswith(f"-{suffix}"):
                    cr = candidate
                    break
        if cr is None:
            aligned.append(ConditionVerificationResult(
                condition_id=cond.condition_id, status="UNTESTED",
            ))
        elif cr.condition_id == cond.condition_id:
            aligned.append(cr)
        else:
            # Downstream Boolean logic is keyed by the contract's IDs. A
            # suffix or positional match is useful only if its canonical ID is
            # carried forward; otherwise IF_THEN can treat it as missing.
            aligned.append(cr.model_copy(update={"condition_id": cond.condition_id}))
    return aligned


def _complete_condition_results_preserving_order(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
) -> list[ConditionVerificationResult]:
    """Keep model output order and append explicit placeholders for omissions."""
    completed = list(condition_results)
    existing = {(result.condition_id or "").lower() for result in completed}
    for condition in contract.atomic_conditions:
        suffix = condition.condition_id.rsplit("-", 1)[-1].lower()
        if any(item == condition.condition_id.lower() or item == suffix or item.endswith(f"-{suffix}") for item in existing):
            continue
        completed.append(ConditionVerificationResult(
            condition_id=condition.condition_id,
            description=condition.description or condition.parameter,
            status="UNTESTED",
            reason="No condition result was supplied for this declared contract condition.",
        ))
    return completed


def _canonicalize_condition_result_ids(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
) -> list[ConditionVerificationResult]:
    """Canonicalize unambiguous short IDs before evidence auditing."""
    exact_ids = {condition.condition_id for condition in contract.atomic_conditions}
    by_suffix: dict[str, list[str]] = {}
    for condition_id in exact_ids:
        by_suffix.setdefault(condition_id.rsplit("-", 1)[-1].lower(), []).append(condition_id)

    canonical: list[ConditionVerificationResult] = []
    for result in condition_results:
        if result.condition_id in exact_ids:
            canonical.append(result)
            continue
        suffix = (result.condition_id or "").rsplit("-", 1)[-1].lower()
        matches = by_suffix.get(suffix, [])
        if len(matches) == 1:
            canonical.append(result.model_copy(update={"condition_id": matches[0]}))
        else:
            canonical.append(result)
    return canonical


# ── Qualification enforcement on condition results ──────────────────────────

def _resolve_evidence_ids(ids: list[str]) -> set[str]:
    """Normalize LLM evidence references ('E2', '2', 'Evidence 3') to E{n}."""
    out: set[str] = set()
    for eid in ids or []:
        m = re.search(r"(\d+)", str(eid))
        if m:
            out.add(f"E{m.group(1)}")
    return out


def _normalized_quote_text(value: str) -> str:
    """Normalize evidence text for conservative citation containment checks."""
    normalized = (value or "").lower().replace("μ", "µ").replace("�", "µ")
    return " ".join(re.findall(r"[a-z0-9µ%]+", normalized))


def _quote_is_traceable(quote: str, contents: list[str]) -> bool:
    """True when the leading factual clause appears in a cited evidence item."""
    quote_tokens = _normalized_quote_text(quote).split()
    if not quote_tokens:
        return False
    # Twelve normalized tokens are long enough to reject invented citations
    # while tolerating a PDF line truncated after the factual clause.
    needle = " ".join(quote_tokens[:12])
    return any(needle in _normalized_quote_text(content) for content in contents if content)


def _quote_is_exactly_traceable(quote: str, content: str) -> bool:
    """Use the complete normalized quote when repairing a citation ID.

    The looser leading-clause check remains useful for validating a citation
    already chosen by the model. Reassigning provenance is a stronger action,
    so it requires the full normalized quotation to occur in the target item.
    """
    normalized_quote = _normalized_quote_text(quote)
    return bool(normalized_quote) and normalized_quote in _normalized_quote_text(content)


def locate_exact_quote_span(quote: str, content: str) -> Optional[tuple[int, int]]:
    """Locate a literal quote inside the exact excerpt shown to the model.

    Unlike the legacy normalized traceability check, this is intentionally
    strict: offsets are emitted only when slicing the source excerpt reproduces
    the quote byte-for-character at the Python string level.
    """
    candidate = (quote or "").strip()
    if not candidate or not content:
        return None
    start = content.find(candidate)
    if start < 0:
        return None
    return start, start + len(candidate)


def _validated_span_refs(
    result: ConditionVerificationResult,
    evidence_contents: dict[str, str],
) -> set[str]:
    """Return evidence IDs whose stored offsets reproduce their exact quote."""
    valid: set[str] = set()
    for span in result.evidence_spans:
        evidence_id = next(iter(_resolve_evidence_ids([span.evidence_id])), "")
        content = evidence_contents.get(evidence_id)
        if not content or span.end_offset > len(content):
            continue
        if content[span.start_offset:span.end_offset] == span.exact_quote:
            valid.add(evidence_id)
    return valid


def condition_attribution_is_traceable(
    result: ConditionVerificationResult,
    evidence_contents: dict[str, str],
) -> bool:
    """Validate that a condition quote occurs in one of its cited evidence IDs."""
    if result.evidence_spans:
        return bool(_validated_span_refs(result, evidence_contents))
    refs = _resolve_evidence_ids(result.evidence_ids)
    if not refs or not result.quote:
        return False
    cited_contents = [evidence_contents[ref] for ref in refs if ref in evidence_contents]
    return bool(cited_contents) and _quote_is_traceable(result.quote, cited_contents)


def _is_contradiction_relevant(q: EvidenceQualification) -> bool:
    """Can this (possibly method-incompatible) evidence refute a condition?

    A component datasheet cannot PROVE a physical test requirement, but a
    hard rating at matching scope and parameter is a genuine contradiction.
    Only scope or parameter mismatches make evidence irrelevant for
    refutation.
    """
    # Refutation needs affirmative scope and parameter alignment. Merely
    # unknown alignment is not enough to turn an unqualified datasheet or
    # unrelated passage into a safety conflict.
    return q.scope_compatible is True and q.parameter_compatible is True


def _add_validation_note(
    result: ConditionVerificationResult,
    state: str,
    note: str,
) -> None:
    """Record validator output without silently replacing semantic meaning."""
    # A confirmed citation failure is stronger than an unresolved heuristic;
    # otherwise the latest qualification result may refine an earlier VALID.
    if result.validation_state != "CONTRADICTED" or state == "CONTRADICTED":
        result.validation_state = state  # type: ignore[assignment]
    if note not in result.validation_notes:
        result.validation_notes.append(note)


_STRICT_REFERENCE_CUE_RE = re.compile(
    r"\b(?:conform(?:s|ing|ance)?|certif(?:y|ied|ication)|specified\s+in|"
    r"compliant\s+with|in\s+accordance\s+with)\b",
    re.IGNORECASE,
)
_STANDARD_REFERENCE_RE = re.compile(
    r"\b(?:\d+\s*CFR(?:\s+part)?\s*\d+(?:\.\d+)*|part\s+\d+(?:\.\d+)*|"
    r"(?:ISO|IEC|SAE|EN|DIN|UL|ASTM|IEEE|FMVSS)\s*(?:No\.?\s*)?\d+(?:[-.:/]\d+)*|"
    r"\d{3,}(?:\.\d+)+)\b",
    re.IGNORECASE,
)
_VISUAL_ATTRIBUTE_STOPWORDS = {
    "a", "an", "and", "are", "be", "has", "have", "is", "it", "of", "on",
    "or", "present", "shown", "the", "there", "visible", "with",
}


def _reference_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _missing_strict_references(
    condition: AtomicConditionContract,
    evidence_text: str,
) -> list[str]:
    assertion = f"{condition.description or ''} {condition.threshold or ''}"
    if not _STRICT_REFERENCE_CUE_RE.search(assertion):
        return []
    references = list(dict.fromkeys(_STANDARD_REFERENCE_RE.findall(assertion)))
    normalized_evidence = _reference_key(evidence_text)
    return [
        reference
        for reference in references
        if _reference_key(reference) not in normalized_evidence
    ]


def _missing_visual_attributes(
    condition: AtomicConditionContract,
    evidence_text: str,
) -> list[str]:
    if not condition.requires_visual_evidence or not isinstance(condition.threshold, str):
        return []
    required = [
        token
        for token in re.findall(r"[a-z0-9]+", condition.threshold.lower())
        if len(token) > 1 and token not in _VISUAL_ATTRIBUTE_STOPWORDS
    ]
    # Presence/visibility conditions are governed by identity and population
    # checks. Attribute grounding applies to compound descriptions such as
    # colour + border + symbol shape.
    if len(required) < 2:
        return []
    observed = set(re.findall(r"[a-z0-9]+", evidence_text.lower()))
    return [token for token in dict.fromkeys(required) if token not in observed]


def _invalidate_unsupported_proof(
    result: ConditionVerificationResult,
    note: str,
) -> None:
    """Conservatively remove proof that its own citation cannot establish."""
    result.status = "INCONCLUSIVE"
    result.relationship = "UNCLEAR"
    _add_validation_note(result, "CONTRADICTED", note)


def audit_condition_evidence(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
    qualifications: list[EvidenceQualification],
    qualified_contents: Optional[dict[str, str]] = None,
) -> list[ConditionVerificationResult]:
    """Audit traceability and conservatively invalidate ungrounded proof.

    Most semantic labels remain model-owned. A PROVEN label is normalized to
    INCONCLUSIVE only when its own cited passage omits an explicitly required
    standard reference or compound visual attribute. The transition is stored
    in diagnostics by ``finalize_verdict``.
    """
    qual_by_id = {q.evidence_id: q for q in qualifications}
    qualified_contents = qualified_contents or {}
    conditions_by_id = {c.condition_id: c for c in contract.atomic_conditions}

    for cr in condition_results:
        cond = conditions_by_id.get(cr.condition_id)
        if cr.status == "UNTESTED":
            relevant_unqualified = any(
                q.qualification_status != "QUALIFIED"
                and q.scope_compatible is not False
                and (cond is None or condition_evidence_compatible(cond, q) is not False)
                for q in qualifications
            )
            if relevant_unqualified:
                _add_validation_note(
                    cr,
                    "UNRESOLVED",
                    "Potentially relevant evidence was not qualified; the LLM UNTESTED status was preserved.",
                )
            else:
                _add_validation_note(cr, "VALID", "No attributed proof claim requires qualification.")
            continue
        if cr.status not in ("PROVEN", "FAILED", "PENDING"):
            continue

        if cr.evidence_spans:
            exact_span_refs = _validated_span_refs(cr, qualified_contents)
            if not exact_span_refs:
                _add_validation_note(
                    cr,
                    "CONTRADICTED",
                    "Stored citation offsets do not reproduce an exact span of their evidence excerpt; "
                    "the LLM status was preserved.",
                )
                continue
            cr.evidence_ids = sorted(
                exact_span_refs,
                key=lambda item: int(re.search(r"\d+", item).group()),
            )
            # One canonical extractive quote keeps existing API/report fields
            # backward compatible while the full span list carries provenance.
            cr.quote = cr.evidence_spans[0].exact_quote

        refs = _resolve_evidence_ids(cr.evidence_ids)
        backing = [qual_by_id[r] for r in refs if r in qual_by_id]
        cited_contents = {
            ref: qualified_contents[ref]
            for ref in refs
            if ref in qualified_contents
        }

        if cr.quote and cited_contents:
            matching_refs = {
                ref
                for ref, content in cited_contents.items()
                if _quote_is_traceable(cr.quote, [content])
            }
            if not matching_refs:
                reconciled_refs = {
                    evidence_id
                    for evidence_id, content in qualified_contents.items()
                    if _quote_is_exactly_traceable(cr.quote, content)
                }
                if reconciled_refs:
                    previous_refs = sorted(refs)
                    cr.evidence_ids = sorted(reconciled_refs, key=lambda item: int(re.search(r"\d+", item).group()))
                    refs = reconciled_refs
                    cited_contents = {
                        ref: qualified_contents[ref]
                        for ref in refs
                        if ref in qualified_contents
                    }
                    matching_refs = reconciled_refs
                    _add_validation_note(
                        cr,
                        "UNRESOLVED",
                        "Citation IDs were reconciled by exact quote traceability from "
                        f"{', '.join(previous_refs) or 'none'} to {', '.join(cr.evidence_ids)}.",
                    )
                else:
                    _add_validation_note(
                        cr,
                        "CONTRADICTED",
                        "The cited quote could not be traced to any retrieved evidence; the LLM status was preserved.",
                    )
                    continue
            backing = [qual_by_id[ref] for ref in matching_refs if ref in qual_by_id]

        # Diagnostics may locate a quote under another retrieved ID, but do not
        # silently rewrite the model's evidence_ids or semantic status.
        if not backing and cr.quote:
            matching_ids = {
                evidence_id
                for evidence_id, content in qualified_contents.items()
                if _quote_is_traceable(cr.quote, [content])
            }
            backing = [qual_by_id[evidence_id] for evidence_id in matching_ids if evidence_id in qual_by_id]

        if not backing:
            _add_validation_note(
                cr,
                "CONTRADICTED",
                "No existing evidence item can be tied to this attributed condition result; "
                "the LLM status was preserved.",
            )
            continue

        if cr.status == "PROVEN":
            ok = any(b.qualification_status == "QUALIFIED" for b in backing)
        elif cr.status == "PENDING":
            ok = any(b.qualification_status in ("QUALIFIED", "PARTIALLY_QUALIFIED") for b in backing)
        else:  # FAILED
            ok = any(
                b.qualification_status == "QUALIFIED" or _is_contradiction_relevant(b)
                for b in backing
            )

        if not ok:
            hard_method_mismatch = (
                cr.status in ("PROVEN", "PENDING")
                and backing
                and all(b.method_compatible is False for b in backing)
            )
            if hard_method_mismatch:
                _add_validation_note(
                    cr,
                    "CONTRADICTED",
                    "The cited evidence method cannot satisfy the required verification method; "
                    "the LLM status was preserved.",
                )
            else:
                _add_validation_note(
                    cr,
                    "UNRESOLVED",
                    "Evidence qualification did not establish admissible proof; the LLM status was preserved.",
                )
            continue

        _add_validation_note(cr, "VALID", "Evidence attribution and admissibility checks passed.")

        cited_text = "\n".join(cited_contents.values())
        if cond is not None:
            # A pending result may legitimately omit an external standard
            # reference while work is still in progress.  Compound visual
            # attributes are different: if the cited image does not show the
            # requested attributes, PENDING overstates what was observed.
            if cr.status == "PROVEN":
                missing_references = _missing_strict_references(cond, cited_text)
            else:
                missing_references = []
            if missing_references:
                _invalidate_unsupported_proof(
                    cr,
                    "The cited passage does not explicitly establish required reference(s): "
                    + ", ".join(missing_references)
                    + ". Proof was normalized to INCONCLUSIVE.",
                )
                continue
            missing_attributes = (
                _missing_visual_attributes(cond, cited_text)
                if cr.status in {"PROVEN", "PENDING"}
                else []
            )
            if missing_attributes:
                _invalidate_unsupported_proof(
                    cr,
                    "The cited visual evidence does not state required attribute(s): "
                    + ", ".join(missing_attributes)
                    + ". Proof was normalized to INCONCLUSIVE.",
                )
                continue

        if (
            cr.status == "PROVEN"
            and cond is not None
            and cond.requires_visual_evidence
            and not any("VISUAL DESCRIPTION:" in content for content in cited_contents.values())
        ):
            _add_validation_note(
                cr,
                "UNRESOLVED",
                "This condition requires visual evidence, but the cited figure was not rendered and described; semantic status was preserved.",
            )

        # Per-condition parameter check on qualified backing.
        if cr.status == "PROVEN" and cond is not None and cond.parameter:
            qualified_backing = [b for b in backing if b.qualification_status == "QUALIFIED"]
            compat = [condition_evidence_compatible(cond, b) for b in qualified_backing]
            cond_param = normalize_parameter(cond.parameter)
            quote_params = set(extract_parameters_from_text(cr.quote or ""))
            quote_supports_parameter = bool(cond_param and cond_param in quote_params)
            if not quote_supports_parameter and compat and all(c is False for c in compat):
                _add_validation_note(
                    cr,
                    "UNRESOLVED",
                    f"Lexical parameter mapping did not confirm condition '{cond.condition_id}' "
                    f"({cond.parameter}); semantic status was preserved.",
                )

    return condition_results


# ── The single final-status decision function ────────────────────────────────

def _logic_tree_condition_ids(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return []
    output: list[str] = []
    if node.get("condition_id"):
        output.append(str(node["condition_id"]))
    for child in node.get("children") or []:
        output.extend(_logic_tree_condition_ids(child))
    for key in ("antecedent", "consequent", "if", "then"):
        output.extend(_logic_tree_condition_ids(node.get(key)))
    return list(dict.fromkeys(output))


def _logic_tree_structure_messages(
    node: Any,
    known_ids: set[str],
    path: str = "logic_tree",
) -> list[str]:
    """Return reasons a nested Boolean tree is unsafe to execute."""
    if not isinstance(node, dict):
        return [f"{path} is not an object"]

    operator = str(node.get("operator") or "").upper()
    if operator == "CONDITION":
        condition_id = str(node.get("condition_id") or "")
        if not condition_id:
            return [f"{path} has no condition_id"]
        if condition_id not in known_ids:
            return [f"{path} references unknown condition {condition_id}"]
        return []

    if operator in {"ALL_OF", "ANY_OF"}:
        children = node.get("children")
        if not isinstance(children, list) or not children:
            return [f"{path}.{operator} has no children"]
        issues: list[str] = []
        if operator == "ANY_OF" and len(children) < 2:
            issues.append(f"{path}.ANY_OF needs at least two alternatives")
        for index, child in enumerate(children):
            issues.extend(_logic_tree_structure_messages(
                child, known_ids, f"{path}.children[{index}]"
            ))
        return issues

    if operator == "IF_THEN":
        antecedent = node.get("antecedent") or node.get("if")
        consequent = node.get("consequent") or node.get("then")
        issues = []
        if antecedent is None:
            issues.append(f"{path}.IF_THEN has no antecedent")
        else:
            issues.extend(_logic_tree_structure_messages(
                antecedent, known_ids, f"{path}.antecedent"
            ))
        if consequent is None:
            issues.append(f"{path}.IF_THEN has no consequent")
        else:
            issues.extend(_logic_tree_structure_messages(
                consequent, known_ids, f"{path}.consequent"
            ))
        return issues

    return [f"{path} has unsupported operator {operator or '<missing>'}"]


def _logic_tree_structure_issues(node: Any, known_ids: set[str], path: str = "logic_tree") -> list[str]:
    return [ValidationIssue(message, "LOGIC_STRUCTURE", message.split(" ", 1)[0])
            for message in _logic_tree_structure_messages(node, known_ids, path)]


def _validated_decisive_status(node: Any, by_id: dict[str, ConditionVerificationResult]) -> Optional[str]:
    """Find a sufficient proof using only VALID evidence, including gate proof."""
    if not isinstance(node, dict):
        return None
    op = node.get("operator")
    if op == "CONDITION":
        result = by_id.get(node.get("condition_id"))
        if result is None or result.validation_state != "VALID":
            return None
        return {"PROVEN": "SUPPORTED", "FAILED": "CONFLICT", "NOT_APPLICABLE": "NOT_APPLICABLE"}.get(result.status)
    if op == "IF_THEN":
        gate = _validated_decisive_status(node.get("antecedent"), by_id)
        if gate == "NOT_APPLICABLE":
            return gate
        return _validated_decisive_status(node.get("consequent"), by_id) if gate == "SUPPORTED" else None
    children = node.get("children") or []
    if op not in {"ALL_OF", "ANY_OF"} or not children:
        return None
    statuses = [_validated_decisive_status(child, by_id) for child in children]
    active = [s for s in statuses if s != "NOT_APPLICABLE"]
    if not active:
        return "NOT_APPLICABLE"
    if op == "ALL_OF":
        if "CONFLICT" in active:
            return "CONFLICT"
        return "SUPPORTED" if all(s == "SUPPORTED" for s in active) else None
    if "SUPPORTED" in active:
        return "SUPPORTED"
    return "CONFLICT" if all(s == "CONFLICT" for s in active) else None


def aggregation_input_issues(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
    original_condition_results: Optional[list[ConditionVerificationResult]] = None,
) -> list[str]:
    """Validate symbolic inputs before they may override the LLM verdict.

    This is stricter than schema validation. The returned list contains both
    advisory quality findings and non-executable input defects. The caller uses
    ``aggregation_blocking_issues`` to decide whether aggregation must abstain.
    ``contract_complete=None`` remains supported for legacy/manual contracts.
    """
    issues: list[str] = []
    conditions = list(contract.atomic_conditions or [])
    condition_ids = [condition.condition_id for condition in conditions]
    known_ids = set(condition_ids)

    if not conditions:
        issues.append("the contract has no atomic conditions")
        return issues
    if len(known_ids) != len(condition_ids):
        issues.append("the contract contains duplicate condition IDs")
    for condition in conditions:
        issues.extend(ValidationIssue(f"condition {condition.condition_id} {message}", "PREDICATE", condition.condition_id)
                      for message in predicate_issues(condition))
    if contract.contract_complete is False:
        issues.append("the extracted atomic contract is incomplete")
    if contract.unmapped_obligations:
        issues.append("one or more source obligations are unmapped")
    if contract.validation_issues:
        issues.append("contract validation reported unresolved issues")
    if contract.ambiguities:
        issues.append("the atomic decomposition contains unresolved ambiguity")
    if (
        contract.decomposition_confidence is not None
        and contract.decomposition_confidence < 0.7
    ):
        issues.append(
            f"decomposition confidence is {contract.decomposition_confidence:.2f}"
        )

    required_verification_ids = {
        condition.condition_id
        for condition in conditions
        if condition.mandatory and condition.condition_role == "VERIFICATION"
    }
    tree = getattr(contract, "logic_tree", None)
    governed_ids: list[str]
    if tree is not None:
        issues.extend(_logic_tree_structure_issues(tree, known_ids))
        governed_ids = _logic_tree_condition_ids(tree)
        missing = required_verification_ids - set(governed_ids)
        if missing:
            issues.append(ValidationIssue(
                "logic_tree omits mandatory verification condition(s): "
                + ", ".join(sorted(missing)), "MISSING_OBLIGATION", "logic_tree"
            ))
    else:
        logic = contract.logic
        operator = str(getattr(logic, "operator", "ALL_OF") or "ALL_OF").upper()
        if operator == "IF_THEN":
            antecedent_id = getattr(logic, "if_condition_id", None)
            consequent_ids = list(getattr(logic, "then_condition_ids", []) or [])
            governed_ids = list(dict.fromkeys(
                ([antecedent_id] if antecedent_id else []) + consequent_ids
            ))
            if not antecedent_id:
                issues.append("flat IF_THEN logic has no antecedent condition")
            if not consequent_ids:
                issues.append("flat IF_THEN logic has no consequent conditions")
        else:
            governed_ids = list(getattr(logic, "condition_ids", []) or [])
            if not governed_ids:
                governed_ids = [
                    condition.condition_id for condition in conditions if condition.mandatory
                ]
            if operator == "ANY_OF" and len(governed_ids) < 2:
                issues.append("flat ANY_OF logic needs at least two alternatives")

        unknown = set(governed_ids) - known_ids
        if unknown:
            issues.append(
                "flat logic references unknown condition(s): "
                + ", ".join(sorted(unknown))
            )
        missing = required_verification_ids - set(governed_ids)
        if missing:
            issues.append(ValidationIssue(
                "flat logic omits mandatory verification condition(s): "
                + ", ".join(sorted(missing)), "MISSING_OBLIGATION", "logic"
            ))

    # Missing LLM results must not silently become authoritative UNTESTED
    # placeholders. Suffix IDs are accepted and canonicalized by the matcher.
    supplied = list(
        original_condition_results
        if original_condition_results is not None
        else condition_results
    )
    for condition_id in governed_ids:
        suffix = condition_id.rsplit("-", 1)[-1].lower()
        matches = [
            result for result in supplied
            if (result.condition_id or "").lower() == condition_id.lower()
            or (result.condition_id or "").lower() == suffix
            or (result.condition_id or "").lower().endswith(f"-{suffix}")
        ]
        if not matches:
            issues.append(f"no condition result was supplied for {condition_id}")
        elif len(matches) > 1:
            issues.append(f"multiple condition results ambiguously match {condition_id}")

    aligned = _match_condition_results(contract, condition_results)
    by_id = {result.condition_id: result for result in aligned}
    # Only a complete, unambiguous contract can justify ignoring an unrelated
    # unresolved result. Existing findings and review requirements stay visible.
    decisive = None
    if not issues and contract.contract_complete is True:
        decisive = _validated_decisive_status(contract_tree(contract), by_id)
        computed, _, _ = aggregate_condition_statuses(contract, condition_results)
        if decisive != computed:
            decisive = None
    for condition_id in governed_ids:
        result = by_id.get(condition_id)
        if result is None:
            continue
        if (
            result.status in {"PROVEN", "FAILED", "PENDING", "NOT_APPLICABLE"}
            and result.validation_state != "VALID"
        ):
            issues.append(ValidationIssue(
                f"condition {condition_id} has {result.status} status with "
                f"{result.validation_state} evidence validation",
                "EVIDENCE_VALIDATION", condition_id, blocking=decisive is None,
            ))

    return list(dict.fromkeys(issues))


def aggregation_blocking_issues(issues: list[str]) -> list[str]:
    """Return only defects that make the available Boolean contract non-executable.

    Completeness and semantic-quality findings remain visible and force review,
    but the aggregator can still calculate the status of the conditions that
    were actually extracted. Missing model results are represented explicitly
    as INCONCLUSIVE placeholders before this check.
    """
    blocking_prefixes = (
        "the contract has no atomic conditions",
        "the contract contains duplicate condition IDs",
        "logic_tree is not an object",
        "logic_tree has unsupported operator",
        "logic_tree references unknown condition",
        "flat IF_THEN logic has no antecedent condition",
        "flat IF_THEN logic has no consequent conditions",
        "flat ANY_OF logic needs at least two alternatives",
        "flat logic references unknown condition",
        "multiple condition results ambiguously match",
        "condition ",
    )
    structural_fragments = (
        ".CONDITION has no condition_id",
        ".ALL_OF has no children",
        ".ANY_OF has no children",
        ".ANY_OF needs at least two alternatives",
        ".IF_THEN has no antecedent",
        ".IF_THEN has no consequent",
    )
    return [
        issue for issue in issues
        if (issue.blocking if isinstance(issue, ValidationIssue) else (
            issue.startswith(blocking_prefixes)
            or (issue.startswith("logic_tree") and any(fragment in issue for fragment in (
                "has unsupported operator", "references unknown condition", "has no condition_id", "is not an object",
            )))
            or any(fragment in issue for fragment in structural_fragments)
        ))
    ]


def _aggregate_status_group(operator: str, statuses: list[str]) -> Optional[str]:
    """Mechanically combine already-interpreted child statuses."""
    active = [status for status in statuses if status != "NOT_APPLICABLE"]
    if not active:
        return "NOT_APPLICABLE"
    if operator == "ANY_OF":
        if "SUPPORTED" in active:
            return "SUPPORTED"
        if "PARTIAL" in active:
            return "PARTIAL"
        if "UNKNOWN" in active:
            return "UNKNOWN"
        if "MISSING" in active:
            return "MISSING"
        if all(status == "CONFLICT" for status in active):
            return "CONFLICT"
        return None

    if "CONFLICT" in active:
        return "CONFLICT"
    if all(status == "SUPPORTED" for status in active):
        return "SUPPORTED"
    if "SUPPORTED" in active or "PARTIAL" in active:
        return "PARTIAL"
    if all(status == "MISSING" for status in active):
        return "MISSING"
    if "UNKNOWN" in active:
        return "UNKNOWN"
    if "MISSING" in active:
        return "MISSING"
    return None


def _aggregate_logic_tree_node(
    node: Any,
    by_id: dict[str, ConditionVerificationResult],
) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    operator = str(node.get("operator") or "").upper()
    if operator == "CONDITION":
        result = by_id.get(str(node.get("condition_id") or ""))
        if result is None:
            return None
        return condition_to_verdict(result.status or "UNTESTED")
    if operator in {"ALL_OF", "ANY_OF"}:
        children = node.get("children") or []
        if not children or (operator == "ANY_OF" and len(children) < 2):
            return None
        statuses = [_aggregate_logic_tree_node(child, by_id) for child in children]
        if any(status is None for status in statuses):
            return None
        return _aggregate_status_group(operator, [str(status) for status in statuses])
    if operator == "IF_THEN":
        antecedent = node.get("antecedent") or node.get("if")
        consequent = node.get("consequent") or node.get("then")
        antecedent_status = _aggregate_logic_tree_node(antecedent, by_id)
        if antecedent_status is None or consequent is None:
            return None
        if antecedent_status == "NOT_APPLICABLE":
            return "NOT_APPLICABLE"
        if antecedent_status != "SUPPORTED":
            return antecedent_status
        return _aggregate_logic_tree_node(consequent, by_id)
    return None


def _aggregate_nested_logic_tree(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
) -> Optional[tuple[str, float, str]]:
    tree = getattr(contract, "logic_tree", None)
    tree_ids = _logic_tree_condition_ids(tree)
    if not tree or not tree_ids:
        return None
    condition_by_id = {condition.condition_id: condition for condition in contract.atomic_conditions}
    if any(condition_id not in condition_by_id for condition_id in tree_ids):
        return None
    tree_conditions = [condition_by_id[condition_id] for condition_id in tree_ids]
    aligned = _match_condition_results(tree_conditions, condition_results)
    by_id = {result.condition_id: result for result in aligned}
    status = _aggregate_logic_tree_node(tree, by_id)
    if status is None:
        return None
    confidence = {
        "SUPPORTED": 95.0,
        "CONFLICT": 95.0,
        "MISSING": 92.0,
        "PARTIAL": 88.0,
        "UNKNOWN": 80.0,
        "NOT_APPLICABLE": 90.0,
    }[status]
    return (
        status,
        confidence,
        f"Mechanical nested-logic aggregation evaluated {len(tree_ids)} declared condition(s).",
    )

def aggregate_condition_statuses(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
    evidence_qualification: Optional[list[EvidenceQualification]] = None,
    has_relevant_evidence: bool = True,
    evidence_absent: bool = False,
) -> tuple[str, float, str]:
    """Mechanically aggregate immutable condition results into the final status.

    Evidence presence and qualification metadata are deliberately ignored here.
    They are audit outputs, not semantic inputs.  The optional legacy arguments
    remain in the signature for backward compatibility with existing callers.

    Returns:
        (status, confidence, reason)
    """
    nested = _aggregate_nested_logic_tree(contract, condition_results)
    if nested is not None:
        return nested
    if getattr(contract, "logic_tree", None) is not None:
        return "UNKNOWN", 70.0, "Mechanical aggregation: the authoritative logic_tree cannot be evaluated."

    aligned = _match_condition_results(contract, condition_results)
    logic = getattr(contract, "logic", None)
    operator = getattr(logic, "operator", "ALL_OF") or "ALL_OF"

    if operator == "IF_THEN":
        by_id = {result.condition_id: result for result in aligned}
        antecedent_id = getattr(logic, "if_condition_id", None)
        consequent_ids = list(getattr(logic, "then_condition_ids", []) or [])
        antecedent = by_id.get(antecedent_id) if antecedent_id else None
        if antecedent is None:
            return (
                "UNKNOWN", 70.0,
                "Mechanical IF_THEN aggregation: the antecedent condition is missing.",
            )
        antecedent_status = (antecedent.status or "UNTESTED").upper()
        if antecedent_status == "NOT_APPLICABLE":
            return (
                "NOT_APPLICABLE", 90.0,
                "Mechanical IF_THEN aggregation: the antecedent does not apply, so this conditional requirement was not evaluated as a pass.",
            )
        if antecedent_status == "UNTESTED":
            if any(result.status == "INCONCLUSIVE" for result in aligned):
                return (
                    "UNKNOWN", 80.0,
                    "Mechanical IF_THEN aggregation: applicability is untested and supplied evidence is inconclusive.",
                )
            return (
                "MISSING", 90.0,
                "Mechanical IF_THEN aggregation: the antecedent was not tested.",
            )
        if antecedent_status == "INCONCLUSIVE":
            return (
                "UNKNOWN", 80.0,
                "Mechanical IF_THEN aggregation: applicability of the antecedent is inconclusive.",
            )
        if antecedent_status == "PENDING":
            return (
                "PARTIAL", 85.0,
                "Mechanical IF_THEN aggregation: applicability of the antecedent remains pending.",
            )
        if antecedent_status == "FAILED":
            return (
                "CONFLICT", 95.0,
                "Mechanical IF_THEN aggregation: the antecedent condition was reported as FAILED rather than not applicable.",
            )
        aligned = [by_id[condition_id] for condition_id in consequent_ids if condition_id in by_id]
        if len(aligned) != len(consequent_ids) or not aligned:
            return (
                "UNKNOWN", 70.0,
                "Mechanical IF_THEN aggregation: one or more consequent conditions are missing.",
            )

    n = len(aligned)
    counts = {"PROVEN": 0, "FAILED": 0, "PENDING": 0, "UNTESTED": 0, "INCONCLUSIVE": 0, "NOT_APPLICABLE": 0}
    for cr in aligned:
        st = (cr.status or "UNTESTED").upper()
        counts[st] = counts.get(st, 0) + 1

    active_n = n - counts["NOT_APPLICABLE"]
    if active_n <= 0:
        return (
            "NOT_APPLICABLE", 90.0,
            "Mechanical aggregation: no applicable mandatory condition was available.",
        )

    if operator == "ANY_OF":
        if counts["PROVEN"] > 0:
            return (
                "SUPPORTED", 95.0,
                f"Mechanical ANY_OF aggregation: {counts['PROVEN']} alternative path(s) are PROVEN.",
            )
        if counts["PENDING"] > 0:
            return (
                "PARTIAL", 88.0,
                f"Mechanical ANY_OF aggregation: {counts['PENDING']} alternative path(s) remain PENDING.",
            )
        if counts["INCONCLUSIVE"] > 0:
            return (
                "UNKNOWN", 80.0,
                f"Mechanical ANY_OF aggregation: {counts['INCONCLUSIVE']} alternative path(s) are INCONCLUSIVE.",
            )
        if counts["UNTESTED"] > 0:
            return (
                "MISSING", 92.0,
                "Mechanical ANY_OF aggregation: no alternative compliance path was tested.",
            )
        if counts["FAILED"] > 0:
            return (
                "CONFLICT", 95.0,
                "Mechanical ANY_OF aggregation: every applicable alternative path FAILED.",
            )
        return (
            "UNKNOWN", 75.0,
            "Mechanical ANY_OF aggregation: no applicable alternative path was available.",
        )

    # 1. Any mandatory failure -> CONFLICT
    if counts["FAILED"] > 0:
        return (
            "CONFLICT", 95.0,
            f"Mechanical aggregation: {counts['FAILED']}/{active_n} applicable mandatory condition(s) FAILED.",
        )

    # 2. All mandatory conditions proven -> SUPPORTED
    if counts["PROVEN"] == active_n:
        return (
            "SUPPORTED", 95.0,
            f"Mechanical aggregation: all {active_n} applicable mandatory condition(s) are PROVEN.",
        )

    # 3. Some proven, remainder pending/untested/inconclusive -> PARTIAL
    if counts["PROVEN"] > 0 and counts["PROVEN"] < active_n:
        return (
            "PARTIAL", 90.0,
            f"Mechanical aggregation: {counts['PROVEN']}/{active_n} applicable mandatory condition(s) PROVEN; "
            f"{active_n - counts['PROVEN']} remain pending or unresolved.",
        )

    # 3b. Partial coverage or formally in-progress work -> PARTIAL
    if counts["PENDING"] > 0:
        return (
            "PARTIAL", 88.0,
            f"Mechanical aggregation: {counts['PENDING']} applicable mandatory condition(s) are PENDING.",
        )

    # 4. All remaining applicable conditions are explicitly UNTESTED -> MISSING
    if counts["UNTESTED"] == active_n:
        return (
            "MISSING", 95.0,
            "Mechanical aggregation: all applicable mandatory conditions are UNTESTED.",
        )

    # 5. At least one condition is relevant but inconclusive -> UNKNOWN
    if counts["INCONCLUSIVE"] > 0:
        return (
            "UNKNOWN", 80.0,
            f"Mechanical aggregation: {counts['INCONCLUSIVE']} applicable mandatory condition(s) are INCONCLUSIVE.",
        )

    # Defensive fallback for an incomplete or non-canonical condition payload.
    return (
        "UNKNOWN", 75.0,
        "Mechanical aggregation: condition statuses did not form a complete canonical verdict.",
    )


# ── Finalization for LLM-produced analyses ───────────────────────────────────

def finalize_verdict(
    contract: RequirementContract,
    analysis: VerificationAnalysisResult,
    qualifications: list[EvidenceQualification],
    qualified_contents: Optional[dict[str, str]] = None,
    has_relevant_evidence: bool = True,
    evidence_absent: bool = False,
) -> VerificationAnalysisResult:
    """Reconcile whole-clause reasoning with audited atomic-condition results."""
    diagnostics = dict(getattr(analysis, "_diagnostics", {}) or {})
    provisional_conditions = _condition_snapshot(list(analysis.condition_results or []))
    # Audit a deep copy so validation metadata is available without changing
    # the reasoner's original objects or their semantic statuses.
    copied_results = [
        result.model_copy(deep=True)
        for result in (analysis.condition_results or [])
    ]
    copied_results = _canonicalize_condition_result_ids(contract, copied_results)
    crs = _complete_condition_results_preserving_order(
        contract,
        copied_results,
    )
    crs = audit_condition_evidence(contract, crs, qualifications, qualified_contents)
    if not crs:
        crs = [ConditionVerificationResult(condition_id=c.condition_id, status="UNTESTED")
               for c in mandatory_conditions(contract)]

    status, confidence, reason = aggregate_condition_statuses(
        contract,
        crs,
        evidence_qualification=qualifications,
        has_relevant_evidence=has_relevant_evidence,
        evidence_absent=evidence_absent,
    )

    provisional = (analysis.status or "").upper()
    advisory_reasons = aggregation_input_issues(
        contract,
        crs,
        original_condition_results=list(analysis.condition_results or []),
    )
    blocking_reasons = aggregation_blocking_issues(advisory_reasons)
    aggregation_eligible = not blocking_reasons
    condition_issue_prefixes = (
        "no condition result was supplied for ",
        "multiple condition results ambiguously match ",
        "condition ",
    )
    contract_validation_issues = [
        issue for issue in advisory_reasons
        if not issue.startswith(condition_issue_prefixes)
    ]
    atomic_status = status
    # With malformed symbolic input there is no sound deterministic override.
    # Keep the holistic decision as an explicitly review-only result. An empty
    # model response remains on the conservative atomic fallback.
    advisory_fallback = bool(
        not aggregation_eligible
        and analysis.condition_results
        and provisional in {
            "SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT",
            "NOT_APPLICABLE",
        }
    )
    if advisory_fallback:
        status = provisional
        confidence = min(float(analysis.confidence), 80.0)
        reason = (
            "Whole-clause provisional status retained because deterministic aggregation abstained: "
            + "; ".join(blocking_reasons)
            + f". The atomic aggregate was {atomic_status}. Mandatory review is required."
        )

    override_note = ""
    if provisional and provisional != status:
        override_note = (
            f" [Python aggregator overrode provisional status '{provisional}' "
            f"based on condition-level results.]"
        )

    if advisory_fallback:
        provisional_reason = (analysis.reason or "").strip()
        final_reason = reason
        if provisional_reason:
            final_reason += f" Whole-clause rationale: {provisional_reason}"
    elif override_note:
        provisional_reason = (analysis.reason or "").strip()
        final_reason = f"{reason}{override_note}"
        if provisional_reason:
            final_reason += f" Provisional model rationale: {provisional_reason}"
    else:
        final_reason = (analysis.reason or "").strip() or reason

    finalized = VerificationAnalysisResult(
        status=status,
        confidence=confidence,
        requirement_conditions=analysis.requirement_conditions,
        condition_results=crs,
        evidence_findings=analysis.evidence_findings,
        reason=final_reason,
        highlight=analysis.highlight,
    )
    qualified_conditions = _condition_snapshot(crs)
    transitions = list(diagnostics.get("condition_transitions", []))
    transitions.extend(_condition_transitions(
        provisional_conditions,
        qualified_conditions,
        "evidence_qualification",
    ))
    diagnostics.update({
        "pre_qualification_status": provisional,
        "pre_qualification_condition_results": provisional_conditions,
        "evidence_audit_condition_results": qualified_conditions,
        # Kept as a compatibility alias for existing exports.
        "post_qualification_condition_results": qualified_conditions,
        "final_status": status,
        "atomic_aggregate_status": atomic_status,
        "atomic_advisory_fallback": advisory_fallback,
        "atomic_advisory_reasons": advisory_reasons,
        "aggregator_blocking_issues": blocking_reasons,
        "aggregator_advisory_issues": [
            issue for issue in advisory_reasons if issue not in blocking_reasons
        ],
        "aggregator_contract_valid": not contract_validation_issues,
        "aggregator_input_valid": aggregation_eligible,
        "aggregator_contract_validation_issues": contract_validation_issues,
        "aggregator_validation_issues": advisory_reasons,
        "aggregator_abstained": bool(blocking_reasons),
        "aggregator_decision": (
            "abstained" if not aggregation_eligible else
            "overrode" if provisional and provisional != status else
            "agreed"
        ),
        "aggregator_overrode_status": bool(
            aggregation_eligible and provisional and provisional != status
        ),
        "condition_transitions": transitions,
        "qualification": [q.model_dump(exclude_none=True) for q in qualifications],
    })
    finalized._diagnostics = diagnostics
    return finalized


# ── Deterministic condition mapping from evidence claims ────────────────────
# Used by the rule-based fallback so it shares the LLM path's semantics:
# explicit evidence -> per-condition statuses -> same aggregator.

def _numeric_condition_status(
    contract: RequirementContract,
    cond: AtomicConditionContract,
    claim: EvidenceClaim,
) -> Optional[str]:
    """Map one numeric claim onto one condition.

    PROVEN  — the claim establishes the condition (value satisfies, or the
              tested envelope covers/reaches the required bound)
    FAILED  — measured violation, or a hard component rating (datasheet /
              architecture spec) that restricts the required capability
    PENDING — the tested envelope overlaps the requirement but does not
              reach the required bound (narrower range: partially verified)
    None    — the claim says nothing usable about this condition
    """
    # Explicit categorical observations (notably IP ratings) can be compared
    # without inventing numeric semantics.
    if (
        claim.claim_type == "boolean"
        and isinstance(claim.value, str)
        and isinstance(cond.threshold, str)
        and (cond.operator or "").strip() in ("==", "=")
    ):
        observed = re.sub(r"[^a-z0-9]", "", claim.value.lower())
        required = re.sub(r"[^a-z0-9]", "", cond.threshold.lower())
        return "PROVEN" if observed == required else "FAILED"

    if claim.claim_type not in ("threshold", "numeric_range", "discrete_sweep"):
        return None
    if not are_units_compatible(claim.unit, cond.unit):
        return None

    op = (cond.operator or "").strip()
    is_hard_rating = claim.source_authority in HARD_RATING_AUTHORITIES

    # Requirement envelope for overlap grading
    r_min, r_max = contract.min_value, contract.max_value
    if r_min is None and r_max is None and cond.min_value is not None and cond.max_value is not None:
        r_min, r_max = cond.min_value, cond.max_value

    def _envelope_overlap(c_min: Optional[float], c_max: Optional[float]) -> bool:
        if c_min is None or c_max is None or r_min is None or r_max is None:
            return True  # cannot grade overlap; stay permissive for PENDING decisions
        return c_min <= r_max and c_max >= r_min

    # Envelope condition (min & max both defined on the condition)
    if cond.min_value is not None and cond.max_value is not None:
        if claim.claim_type == "numeric_range" and claim.min_value is not None and claim.max_value is not None:
            c_min = convert_value(claim.min_value, claim.unit, cond.unit)
            c_max = convert_value(claim.max_value, claim.unit, cond.unit)
            if c_min is None or c_max is None:
                return None
            if c_min <= cond.min_value and c_max >= cond.max_value:
                return "PROVEN"   # tested envelope is a superset
            if c_max < cond.min_value or c_min > cond.max_value:
                return "FAILED" if is_hard_rating else None  # disjoint: rating conflict vs untested
            return "PENDING"      # overlaps but does not cover — partially verified
        if claim.claim_type == "discrete_sweep" and claim.discrete_points:
            pts = [p for p in (convert_value(p, claim.unit, cond.unit) for p in claim.discrete_points) if p is not None]
            if not pts:
                return None
            if min(pts) <= cond.min_value and max(pts) >= cond.max_value:
                return "PROVEN"
            if min(pts) > cond.max_value or max(pts) < cond.min_value:
                return None
            return "PENDING"
        return None

    threshold = cond.threshold
    if threshold is None:
        threshold = cond.max_value if op in ("<=", "<") else cond.min_value
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return None
    th = float(threshold)

    # Single measured value: direct satisfaction or genuine violation.
    if claim.claim_type == "threshold" and claim.value is not None and not isinstance(claim.value, (str, bool)):
        v = convert_value(float(claim.value), claim.unit, cond.unit)
        if v is None:
            return None
        if "tolerance" in (cond.parameter or "").lower():
            nominal = next(
                (
                    sibling.threshold
                    for sibling in contract.atomic_conditions
                    if sibling.condition_id != cond.condition_id
                    and sibling.operator in ("==", "=")
                    and isinstance(sibling.threshold, (int, float))
                    and are_units_compatible(sibling.unit, cond.unit)
                ),
                None,
            )
            if nominal is not None:
                v = abs(v - float(nominal))
        if op in (">=", ">"):
            return "PROVEN" if v >= th else "FAILED"
        if op in ("<=", "<"):
            return "PROVEN" if v <= th else "FAILED"
        if op in ("==", "="):
            # A nominal target and its explicit sibling tolerance form one
            # composite interval. Fall back to a small engineering tolerance
            # only when the contract provides no explicit tolerance condition.
            sibling_tolerance = next(
                (
                    float(sibling.threshold)
                    for sibling in contract.atomic_conditions
                    if sibling.condition_id != cond.condition_id
                    and "tolerance" in (sibling.parameter or "").lower()
                    and isinstance(sibling.threshold, (int, float))
                    and are_units_compatible(sibling.unit, cond.unit)
                ),
                None,
            )
            tolerance = sibling_tolerance if sibling_tolerance is not None else max(abs(th) * 0.025, 1e-9)
            if abs(v - th) <= tolerance:
                return "PROVEN"
            return "FAILED" if is_hard_rating else None
        return None

    # Range claim against a bound condition: worst-case semantics.
    if claim.claim_type == "numeric_range" and claim.min_value is not None and claim.max_value is not None:
        c_min = convert_value(claim.min_value, claim.unit, cond.unit)
        c_max = convert_value(claim.max_value, claim.unit, cond.unit)
        if c_min is None or c_max is None:
            return None
        if op in (">=", ">"):
            if c_max >= th:
                return "PROVEN"    # tested up to (or beyond) the required bound
            if is_hard_rating:
                return "FAILED"    # rated below the required capability
            return "PENDING" if _envelope_overlap(c_min, c_max) else None
        if op in ("<=", "<"):
            if c_min <= th:
                return "PROVEN"    # tested down to (or below) the required bound
            if is_hard_rating:
                return "FAILED"
            return "PENDING" if _envelope_overlap(c_min, c_max) else None
        return None

    # Discrete sweep against a bound condition.
    if claim.claim_type == "discrete_sweep" and claim.discrete_points:
        pts = [p for p in (convert_value(p, claim.unit, cond.unit) for p in claim.discrete_points) if p is not None]
        if not pts:
            return None
        if op in (">=", ">") and max(pts) >= th:
            return "PROVEN"
        if op in ("<=", "<") and min(pts) <= th:
            return "PROVEN"
        return None

    return None


def condition_results_from_claims(
    contract: RequirementContract,
    claims: list[EvidenceClaim],
    qualifications: list[EvidenceQualification],
) -> list[ConditionVerificationResult]:
    """Build per-condition results from qualified claims (deterministic path).

    PROVEN requires QUALIFIED evidence. FAILED (violations) additionally
    accepts contradiction-relevant evidence (scope/parameter matched hard
    ratings). PASS verdicts from qualified sources map only onto conditions
    whose parameter they explicitly address — a blanket 'Result: PASS' never
    proves unmeasured conditions of a compound requirement.
    """
    mand = mandatory_conditions(contract)
    qual_by_doc: dict[str, list[EvidenceQualification]] = {}
    qual_by_chunk: dict[str, list[EvidenceQualification]] = {}
    for q in qualifications:
        qual_by_doc.setdefault(q.document_name, []).append(q)
        if q.source_chunk_id:
            qual_by_chunk.setdefault(q.source_chunk_id, []).append(q)

    results: list[ConditionVerificationResult] = []

    # This function is the conservative no-LLM fallback. These phrases do not
    # make a passage irrelevant, but they do mean that a locally parsed number
    # or PASS token is not sufficient for automatic proof. The semantic LLM
    # still receives the complete passage and may resolve the qualification.
    inconclusive_observation_markers = (
        "preliminary", "uncalibrated", "calibration remains", "remains unproven",
        "not representative", "open air", "engineering only", "draft result",
    )

    for cond in mand:
        status: str = "UNTESTED"
        reason = "No qualified evidence addresses this condition."
        evidence_ids: list[str] = []
        quote: Optional[str] = None
        attribution_quality = -1
        violation = False
        pending_found = False
        inconclusive_found = False

        for claim in claims:
            # Prefer the exact originating chunk. Falling back to document name
            # preserves compatibility with legacy/imported claims that predate
            # source_chunk_id, without mixing unrelated pages in normal runs.
            claim_quals = (
                qual_by_chunk.get(claim.source_chunk_id, [])
                if claim.source_chunk_id
                else qual_by_doc.get(claim.document_name, [])
            )
            if not claim_quals:
                continue
            inconclusive_found = True
            directly_qualified = [
                q for q in claim_quals if q.qualification_status == "QUALIFIED"
            ]
            linked_qualified: list[EvidenceQualification] = []
            has_qualified = bool(directly_qualified)
            # A compliance-matrix excerpt may carry the complete observed
            # value when PDF layout truncates the referenced report. Accept it
            # only when that exact authoritative document was independently
            # retrieved and qualified; the matrix alone remains non-proof.
            if not has_qualified and claim.source_authority == "COMPLIANCE_MATRIX":
                quote_lower = claim.quote.lower()
                linked_qualified = [
                    q
                    for q in qualifications
                    if q.qualification_status == "QUALIFIED"
                    and q.document_name.lower() in quote_lower
                ]
                has_qualified = bool(linked_qualified)
            any_contradiction_relevant = any(_is_contradiction_relevant(q) for q in claim_quals)

            claim_text_lower = (claim.quote or "").lower()
            locally_inconclusive = any(
                marker in claim_text_lower for marker in inconclusive_observation_markers
            )

            # Parameter gate: skip claims whose evidence discusses a
            # demonstrably different quantity than this condition.
            claim_parameter_match = parameters_compatible(cond.parameter, claim.parameter, claim.quote)
            is_tolerance_from_nominal = False
            if "tolerance" in (cond.parameter or "").lower() and claim.claim_type == "threshold":
                is_tolerance_from_nominal = any(
                    sibling.condition_id != cond.condition_id
                    and sibling.operator in ("==", "=")
                    and parameters_compatible(sibling.parameter, claim.parameter, claim.quote) is True
                    and are_units_compatible(sibling.unit, cond.unit)
                    for sibling in contract.atomic_conditions
                )
            if claim.parameter and claim_parameter_match is False and not is_tolerance_from_nominal:
                continue
            if not claim.parameter and all(condition_evidence_compatible(cond, q) is False for q in claim_quals):
                continue

            if locally_inconclusive:
                # Preserve the fact that evidence addresses the condition, but
                # never let the deterministic fallback auto-close it.
                inconclusive_found = True
                continue

            st = _numeric_condition_status(contract, cond, claim)
            if st == "PROVEN" and has_qualified:
                status = "PROVEN"
                candidate_ids = [q.evidence_id for q in directly_qualified]
                candidate_quality = 2
                if not candidate_ids and linked_qualified:
                    # Keep the matrix quote and its backing report IDs together.
                    # The matrix supplies the complete excerpt; the independently
                    # retrieved report supplies empirical authority.
                    candidate_ids = [q.evidence_id for q in claim_quals] + [
                        q.evidence_id for q in linked_qualified
                    ]
                    candidate_quality = 1
                if candidate_ids and candidate_quality >= attribution_quality:
                    evidence_ids = list(dict.fromkeys(candidate_ids))
                    quote = claim.quote[:160]
                    reason = f"Condition established by qualified evidence: '{claim.quote[:120]}'"
                    attribution_quality = candidate_quality
            elif st == "FAILED" and (has_qualified or any_contradiction_relevant):
                violation = True
                candidate_ids = [
                    q.evidence_id
                    for q in claim_quals
                    if q.qualification_status == "QUALIFIED" or _is_contradiction_relevant(q)
                ]
                candidate_quality = 2 if directly_qualified else 1
                if candidate_ids and candidate_quality >= attribution_quality:
                    evidence_ids = list(dict.fromkeys(candidate_ids))
                    quote = claim.quote[:160]
                    reason = f"Evidence violates condition {cond.condition_id}: '{claim.quote[:120]}'"
                    attribution_quality = candidate_quality
            elif st == "PENDING" and has_qualified:
                pending_found = True
                candidate_ids = [q.evidence_id for q in directly_qualified]
                if candidate_ids and 2 >= attribution_quality:
                    evidence_ids = list(dict.fromkeys(candidate_ids))
                    quote = claim.quote[:160]
                    reason = "Qualified evidence covers only part of this condition's required bounds."
                    attribution_quality = 2

            # Formal verdict mapping: only onto conditions the verdict
            # addresses. Failure dominates proof for safety-critical results.
            if claim.claim_type == "test_verdict" and has_qualified:
                compatibility = [condition_evidence_compatible(cond, q) for q in claim_quals]
                # A bare PASS cannot prove an unnamed condition. Require an
                # affirmative parameter match or an explicit requirement ID;
                # ambiguous cases belong to the semantic LLM.
                addresses = any(item is True for item in compatibility) or (
                    contract.req_code.lower() in claim_text_lower
                    and all(q.parameter_compatible is not False for q in claim_quals)
                )
                verdict = (claim.test_result or "").upper()
                if addresses and verdict == "FAIL":
                    violation = True
                    candidate_ids = [q.evidence_id for q in directly_qualified]
                    if candidate_ids and 2 >= attribution_quality:
                        evidence_ids = list(dict.fromkeys(candidate_ids))
                        quote = claim.quote[:160]
                        reason = f"Qualified verification record reports FAIL for this condition: '{claim.quote[:120]}'"
                        attribution_quality = 2
                elif addresses and verdict == "PASS":
                    status = "PROVEN"
                    candidate_ids = [q.evidence_id for q in directly_qualified]
                    if candidate_ids and 2 >= attribution_quality:
                        evidence_ids = list(dict.fromkeys(candidate_ids))
                        quote = claim.quote[:160]
                        reason = f"Qualified verification record reports PASS covering this condition: '{claim.quote[:120]}'"
                        attribution_quality = 2

        if violation:
            final_status = "FAILED"
        elif status == "PROVEN":
            final_status = "PROVEN"
        elif pending_found:
            final_status = "PENDING"
            reason = "Qualified evidence covers only part of this condition's required bounds."
        elif inconclusive_found:
            final_status = "INCONCLUSIVE"
            reason = "Relevant evidence exists, but it is not qualified to establish this condition."
        else:
            final_status = "UNTESTED"

        results.append(ConditionVerificationResult(
            condition_id=cond.condition_id,
            description=cond.description,
            status=final_status,
            evidence_ids=list(set(evidence_ids)),
            quote=quote,
            reason=reason,
        ))

    return results
