"""Python-owned final verdict aggregation.

Architecture principle:
    LLM  = interpretation of language / evidence  (produces condition_results)
    Python = deterministic validation + FINAL verdict aggregation

No LLM-produced top-level status is ever passed through un-recomputed.
Every verification path (LLM reasoner and deterministic fallback) funnels
its condition-level results through `aggregate_condition_statuses`, which
is the single place where a requirement's final status is decided.

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
from app.schemas.claim import EvidenceClaim
from app.schemas.verification_result import (
    ConditionVerificationResult,
    VerificationAnalysisResult,
)
from app.schemas.evidence_qualification import EvidenceQualification
from app.services.evidence_qualification import condition_evidence_compatible
from app.services.units import convert_value, are_units_compatible

logger = logging.getLogger("traceaudit.aggregator")

HARD_RATING_AUTHORITIES = ("DATASHEET", "ARCHITECTURE_SPEC")


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
    shorten 'REQ-AUT-001-C2' to 'C2'). Unmatched conditions become UNTESTED.
    When the contract has no atomic decomposition, the returned results are
    used as-is (the producer performed the decomposition).
    """
    if isinstance(contract, list):
        mand = [c for c in contract if getattr(c, "mandatory", True)]
    elif hasattr(contract, "atomic_conditions"):
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
        if cr is None and len(condition_results) == len(mand):
            cr = condition_results[idx]  # positional fallback
        aligned.append(cr if cr is not None else ConditionVerificationResult(
            condition_id=cond.condition_id, status="UNTESTED",
        ))
    return aligned


# ── Qualification enforcement on condition results ──────────────────────────

def _resolve_evidence_ids(ids: list[str]) -> set[str]:
    """Normalize LLM evidence references ('E2', '2', 'Evidence 3') to E{n}."""
    out: set[str] = set()
    for eid in ids or []:
        m = re.search(r"(\d+)", str(eid))
        if m:
            out.add(f"E{m.group(1)}")
    return out


def _is_contradiction_relevant(q: EvidenceQualification) -> bool:
    """Can this (possibly method-incompatible) evidence refute a condition?

    A component datasheet cannot PROVE a physical test requirement, but a
    hard rating at matching scope and parameter is a genuine contradiction.
    Only scope or parameter mismatches make evidence irrelevant for
    refutation.
    """
    return q.scope_compatible is not False and q.parameter_compatible is not False


def merge_qualification_into_conditions(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
    qualifications: list[EvidenceQualification],
    qualified_contents: Optional[dict[str, str]] = None,
) -> list[ConditionVerificationResult]:
    """Enforce evidence qualification on condition-level claims.

    PROVEN requires at least one QUALIFIED backing (explicit evidence_ids,
    or an exact quote inside a qualified chunk — citation check).
    FAILED additionally survives on contradiction-relevant evidence
    (scope/parameter matched, even when the verification method is not).
    """
    qual_by_id = {q.evidence_id: q for q in qualifications}
    qualified_contents = qualified_contents or {}
    conditions_by_id = {c.condition_id: c for c in contract.atomic_conditions}

    for cr in condition_results:
        if cr.status not in ("PROVEN", "FAILED"):
            continue

        refs = _resolve_evidence_ids(cr.evidence_ids)
        backing = [qual_by_id[r] for r in refs if r in qual_by_id]

        # Citation fallback: an exact quote inside a qualified chunk counts
        # as a traceable reference even when evidence_ids were omitted.
        if not any(b.qualification_status == "QUALIFIED" for b in backing) and cr.quote:
            needle = cr.quote.strip().lower()[:80]
            if any(needle in content.lower() for content in qualified_contents.values()):
                backing = [q for q in qualifications if q.qualification_status == "QUALIFIED"]

        if cr.status == "PROVEN":
            ok = any(b.qualification_status == "QUALIFIED" for b in backing)
        else:  # FAILED
            ok = any(
                b.qualification_status == "QUALIFIED" or _is_contradiction_relevant(b)
                for b in backing
            )

        if not ok:
            old = cr.status
            cr.status = "INCONCLUSIVE"
            cr.reason = (
                (cr.reason or "") +
                f" [Downgraded from {old}: referenced evidence is not qualified to "
                f"establish or refute this requirement's conditions.]"
            ).strip()
            continue

        # Per-condition parameter check on qualified backing.
        cond = conditions_by_id.get(cr.condition_id)
        if cr.status == "PROVEN" and cond is not None and cond.parameter:
            qualified_backing = [b for b in backing if b.qualification_status == "QUALIFIED"]
            compat = [condition_evidence_compatible(cond, b) for b in qualified_backing]
            if compat and all(c is False for c in compat):
                cr.status = "INCONCLUSIVE"
                cr.reason = (
                    (cr.reason or "") +
                    f" [Downgraded from PROVEN: qualified evidence discusses a different "
                    f"parameter than condition '{cond.condition_id}' ({cond.parameter}).]"
                ).strip()

    return condition_results


# ── The single final-status decision function ────────────────────────────────

def aggregate_condition_statuses(
    contract: RequirementContract,
    condition_results: list[ConditionVerificationResult],
    evidence_qualification: Optional[list[EvidenceQualification]] = None,
    has_relevant_evidence: bool = True,
    evidence_absent: bool = False,
) -> tuple[str, float, str]:
    """Deterministically aggregate condition results into the FINAL status.

    Args:
        contract: the requirement contract (source of mandatory conditions)
        condition_results: per-condition outcomes (possibly LLM-produced,
            must already be qualification-merged by the caller)
        evidence_qualification: qualifications of the evidence considered
        has_relevant_evidence: any independent (non-spec) evidence retrieved
        evidence_absent: an authoritative record explicitly states evidence
            does not exist (e.g. compliance matrix 'NOT STARTED')

    Returns:
        (status, confidence, reason)
    """
    aligned = _match_condition_results(contract, condition_results)
    n = len(aligned)
    counts = {"PROVEN": 0, "FAILED": 0, "PENDING": 0, "UNTESTED": 0, "INCONCLUSIVE": 0, "NOT_APPLICABLE": 0}
    for cr in aligned:
        st = (cr.status or "UNTESTED").upper()
        counts[st] = counts.get(st, 0) + 1

    # 1. Any mandatory failure -> CONFLICT (highest precedence: safety)
    if counts["FAILED"] > 0:
        return (
            "CONFLICT", 95.0,
            f"Deterministic aggregation: {counts['FAILED']}/{n} mandatory condition(s) FAILED "
            f"(violated by qualified or contradiction-relevant evidence).",
        )

    # 2. All mandatory conditions proven -> SUPPORTED
    if n > 0 and counts["PROVEN"] == n:
        return (
            "SUPPORTED", 95.0,
            f"Deterministic aggregation: all {n} mandatory condition(s) PROVEN by qualified evidence.",
        )

    # 3. Some proven, remainder pending/untested/inconclusive -> PARTIAL
    if counts["PROVEN"] > 0 and counts["PROVEN"] < n:
        return (
            "PARTIAL", 90.0,
            f"Deterministic aggregation: {counts['PROVEN']}/{n} mandatory condition(s) PROVEN; "
            f"{n - counts['PROVEN']} remain pending or unverified.",
        )

    # 3b. Partial coverage or formally in-progress work -> PARTIAL
    if counts["PENDING"] > 0:
        return (
            "PARTIAL", 88.0,
            f"Deterministic aggregation: evidence covers only part of the required bounds or "
            f"verification is in progress ({counts['PENDING']} condition(s) PENDING, none fully proven).",
        )

    # 4. Authoritative record says the evidence does not exist -> MISSING
    if evidence_absent:
        return (
            "MISSING", 95.0,
            "Deterministic aggregation: authoritative record confirms required evidence "
            "has not been produced (not started / missing).",
        )

    # 5. Relevant evidence exists but no condition could be established -> UNKNOWN
    if has_relevant_evidence:
        return (
            "UNKNOWN", 80.0,
            "Deterministic aggregation: relevant evidence retrieved, but no mandatory condition "
            "could be established by qualified evidence (unqualified modality, scope, parameter, "
            "or insufficient detail).",
        )

    # 6. No meaningful evidence -> MISSING
    return (
        "MISSING", 92.0,
        "Deterministic aggregation: no independent evidence addresses this requirement.",
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
    """Recompute the final status of an LLM (or rule-based) analysis in Python.

    The input analysis's top-level `status` is IGNORED. Only its
    condition-level findings survive, after qualification enforcement.
    """
    crs = list(analysis.condition_results or [])
    crs = merge_qualification_into_conditions(contract, crs, qualifications, qualified_contents)
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
    override_note = ""
    if provisional and provisional != status:
        override_note = (
            f" [Python aggregator overrode provisional status '{provisional}' "
            f"based on condition-level results.]"
        )

    final_reason = f"{(analysis.reason or '').strip()}{override_note}" or (reason + override_note)

    return VerificationAnalysisResult(
        status=status,
        confidence=confidence,
        requirement_conditions=analysis.requirement_conditions,
        condition_results=crs,
        evidence_findings=analysis.evidence_findings,
        reason=final_reason,
        highlight=analysis.highlight,
    )


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
    if threshold is None or isinstance(threshold, (str, bool)):
        return None
    th = float(threshold)

    # Single measured value: direct satisfaction or genuine violation.
    if claim.claim_type == "threshold" and claim.value is not None and not isinstance(claim.value, (str, bool)):
        v = convert_value(float(claim.value), claim.unit, cond.unit)
        if v is None:
            return None
        if op in (">=", ">"):
            return "PROVEN" if v >= th else "FAILED"
        if op in ("<=", "<"):
            return "PROVEN" if v <= th else "FAILED"
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
    for q in qualifications:
        qual_by_doc.setdefault(q.document_name, []).append(q)

    results: list[ConditionVerificationResult] = []

    for cond in mand:
        status: str = "UNTESTED"
        reason = "No qualified evidence addresses this condition."
        evidence_ids: list[str] = []
        quote: Optional[str] = None
        violation = False
        pending_found = False

        for claim in claims:
            claim_quals = qual_by_doc.get(claim.document_name, [])
            if not claim_quals:
                continue
            has_qualified = any(q.qualification_status == "QUALIFIED" for q in claim_quals)
            any_contradiction_relevant = any(_is_contradiction_relevant(q) for q in claim_quals)

            # Parameter gate: skip claims whose evidence discusses a
            # demonstrably different quantity than this condition.
            if all(condition_evidence_compatible(cond, q) is False for q in claim_quals):
                continue

            st = _numeric_condition_status(contract, cond, claim)
            if st == "PROVEN" and has_qualified:
                status = "PROVEN"
                evidence_ids.append(claim.claim_id)
                quote = quote or claim.quote[:160]
                reason = f"Condition established by qualified evidence: '{claim.quote[:120]}'"
            elif st == "FAILED" and (has_qualified or any_contradiction_relevant):
                violation = True
                evidence_ids.append(claim.claim_id)
                quote = quote or claim.quote[:160]
                reason = f"Evidence violates condition {cond.condition_id}: '{claim.quote[:120]}'"
            elif st == "PENDING" and has_qualified:
                pending_found = True
                quote = quote or claim.quote[:160]

            # PASS verdict mapping: only onto conditions the verdict addresses
            if (claim.claim_type == "test_verdict" and (claim.test_result or "").upper() == "PASS"
                    and has_qualified):
                addresses = any(condition_evidence_compatible(cond, q) is True for q in claim_quals) \
                    or len(mand) == 1
                if addresses:
                    status = "PROVEN"
                    evidence_ids.append(claim.claim_id)
                    quote = quote or claim.quote[:160]
                    reason = f"Qualified verification record reports PASS covering this condition: '{claim.quote[:120]}'"

        if violation:
            final_status = "FAILED"
        elif status == "PROVEN":
            final_status = "PROVEN"
        elif pending_found:
            final_status = "PENDING"
            reason = "Qualified evidence covers only part of this condition's required bounds."
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
