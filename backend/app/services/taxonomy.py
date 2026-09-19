"""Canonical status taxonomy for TraceAudit.

Single source of truth for every controlled vocabulary in the pipeline:

    condition status  ->  verdict status  ->  display status  ->  review state

Every consumer (classification, verdict aggregation, pipeline persistence)
must import its mappings from here instead of declaring local copies, so the
taxonomy can never drift between the deterministic precheck path, the LLM
verification path, and the persistence layer.

Convention (mirrors evaluation/extraction_benchmark/end_to_end_data.py):
    SUPPORTED -> Reviewed   the machine positively established the answer;
    CONFLICT  -> Reviewed   a contradiction was definitively found. In both
                            cases the verification task itself is complete and
                            any engineering follow-up happens through findings.
    PARTIAL / MISSING / UNKNOWN -> Needs review  the verdict is not final:
                            evidence is incomplete, ambiguous, or absent and
                            a human must look before sign-off.
    "Open" is reserved as the pre-verification placeholder (DB default) and
    is never assigned by the machine verdict.
"""

from app.schemas.enum_normalization import coerce_enum
from typing import Optional

# ── Condition-level atomic statuses ──────────────────────────────────────────
CONDITION_STATUSES = (
    "PROVEN",
    "FAILED",
    "PENDING",
    "UNTESTED",
    "INCONCLUSIVE",
    "NOT_APPLICABLE",
)

# ── Requirement-level verdict statuses ───────────────────────────────────────
VERDICT_STATUSES = (
    "SUPPORTED",
    "PARTIAL",
    "CONFLICT",
    "MISSING",
    "UNKNOWN",
    "NOT_APPLICABLE",
)

# ── Review lifecycle states (human-review layer on top of machine verdicts) ──
REVIEW_STATES = ("Reviewed", "Needs review", "Open")

# Condition status -> requirement verdict status (mechanical aggregation step).
CONDITION_TO_VERDICT = {
    "PROVEN": "SUPPORTED",
    "FAILED": "CONFLICT",
    "PENDING": "PARTIAL",
    "UNTESTED": "MISSING",
    "INCONCLUSIVE": "UNKNOWN",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
}

# Verdict status -> (display status, machine-assigned review state).
VERDICT_TO_DISPLAY_AND_REVIEW = {
    "SUPPORTED": ("Supported", "Reviewed"),
    "PARTIAL": ("Partial", "Needs review"),
    "CONFLICT": ("Conflict", "Reviewed"),
    "MISSING": ("Missing", "Needs review"),
    "UNKNOWN": ("Unknown", "Needs review"),
    "NOT_APPLICABLE": ("Not applicable", "Needs review"),
}

# Display status -> Finding type label used at persistence time.
FINDING_TYPES = {
    "Missing": "Missing evidence",
    "Partial": "Partial evidence",
    "Conflict": "Potential conflict",
    "Unknown": "Inconclusive evidence",
}


def normalize_condition_status(raw: Optional[str]) -> str:
    """Invalid output is a validation failure, not evidence that no test ran."""
    from app.schemas.verification_result import normalize_condition_status as normalize
    return normalize(raw)


def condition_to_verdict(raw: Optional[str]) -> str:
    """Map a condition status to the corresponding requirement verdict status."""
    return CONDITION_TO_VERDICT[normalize_condition_status(raw)]


def normalize_verdict_status(raw: Optional[str]) -> str:
    """Normalize a verdict status (also accepting the display forms)."""
    display_to_verdict = {
        display.upper(): verdict
        for verdict, (display, _review) in VERDICT_TO_DISPLAY_AND_REVIEW.items()
    }
    coerced = coerce_enum(raw or "", VERDICT_STATUSES)
    if coerced:
        return coerced
    return display_to_verdict.get(str(raw or "").strip().upper(), "UNKNOWN")


def display_status(verdict: str) -> str:
    """Display label for a verdict status (defaults to Unknown)."""
    display, _review = VERDICT_TO_DISPLAY_AND_REVIEW.get(
        normalize_verdict_status(verdict), ("Unknown", "Needs review")
    )
    return display


def review_state_for(verdict: str) -> str:
    """Machine-assigned review state for a verdict status."""
    _display, review = VERDICT_TO_DISPLAY_AND_REVIEW.get(
        normalize_verdict_status(verdict), ("Unknown", "Needs review")
    )
    return review


def finding_type_for(display: str) -> str:
    """Finding type label for a display status (defaults to Partial evidence)."""
    return FINDING_TYPES.get(display, "Partial evidence")


SEVERITY_LEVELS = ("Critical", "High", "Medium", "Low")


def normalize_severity(raw: Optional[str], default: str = "Medium") -> str:
    """Normalize domain ratings (e.g. ASIL D/C/B/A, Visual) and free-text strings into canonical
    audit severities: Critical, High, Medium, Low."""
    if not raw:
        return default
    s = str(raw).strip().upper()
    if "ASIL D" in s or "CRIT" in s:
        return "Critical"
    if "ASIL C" in s or "HIGH" in s:
        return "High"
    if "ASIL B" in s or "MED" in s:
        return "Medium"
    if "ASIL A" in s or "LOW" in s or "VISUAL" in s or "QM" in s:
        return "Low"
    return default


def finding_severity_for(
    req_severity: Optional[str],
    coverage_status: Optional[str] = None,
    default: str = "Medium",
) -> str:
    """Derive appropriate finding severity considering the requirement severity and audit coverage status."""
    sev = normalize_severity(req_severity, default=default)
    # A confirmed requirement conflict represents an active non-conformance: at least High
    if coverage_status in ("Conflict", "Potential conflict") and sev in ("Low", "Medium"):
        return "High"
    # Missing evidence on safety-critical requirements is Critical
    if coverage_status in ("Missing", "Missing evidence") and (
        (req_severity and "ASIL D" in req_severity.upper()) or sev == "Critical"
    ):
        return "Critical"
    return sev

