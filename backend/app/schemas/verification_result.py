"""Pydantic Schema for Structured Multi-Condition Verification Reasoning."""

import re
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, PrivateAttr, field_validator
from pydantic.json_schema import SkipJsonSchema


AtomicConditionStatus = Literal["PROVEN", "FAILED", "PENDING", "UNTESTED", "NOT_APPLICABLE", "INCONCLUSIVE"]
VerificationTopLevelStatus = Literal["SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT"]
ConditionValidationState = Literal["VALID", "UNRESOLVED", "CONTRADICTED"]
EvidenceRelationship = Literal["SATISFIES", "VIOLATES", "PARTIAL_COVERAGE", "NOT_ADDRESSED", "UNCLEAR"]
EvidenceValueRole = Literal["OBSERVED", "REQUIRED_OR_PLANNED", "STATUS_ONLY", "NOT_ADDRESSED", "UNCLEAR"]


# Canonical synonym mapping dictionaries for real-world enterprise engineering vocabularies
_CONDITION_STATUS_SYNONYMS: dict[str, AtomicConditionStatus] = {
    # Proven / Passed / Conforming
    "PROVEN": "PROVEN",
    "PASS": "PROVEN",
    "PASSED": "PROVEN",
    "VERIFIED": "PROVEN",
    "MET": "PROVEN",
    "SUCCESS": "PROVEN",
    "SATISFIED": "PROVEN",
    "CONFORMING": "PROVEN",
    "COMPLIANT": "PROVEN",
    "OK": "PROVEN",
    "I_O": "PROVEN",
    "BESTANDEN": "PROVEN",
    # Failed / Violated / Non-Compliant
    "FAILED": "FAILED",
    "FAIL": "FAILED",
    "VIOLATED": "FAILED",
    "NOT_MET": "FAILED",
    "NON_COMPLIANT": "FAILED",
    "NON_CONFORMING": "FAILED",
    "NICHT_BESTANDEN": "FAILED",
    "EXCEEDED": "FAILED",
    "BREACHED": "FAILED",
    "N_I_O": "FAILED",
    # Pending / In Progress / Partial
    "PENDING": "PENDING",
    "IN_PROGRESS": "PENDING",
    "PARTIAL": "PENDING",
    "PARTIALLY_TESTED": "PENDING",
    "DEFERRED": "PENDING",
    "ONGOING": "PENDING",
    "INCOMPLETE": "PENDING",
    "SCHEDULED": "PENDING",
    # Untested / Not Started / Missing
    "UNTESTED": "UNTESTED",
    "NOT_STARTED": "UNTESTED",
    "MISSING": "UNTESTED",
    "NOT_TESTED": "UNTESTED",
    "NOT_PERFORMED": "UNTESTED",
    "NO_EVIDENCE": "UNTESTED",
    "OPEN": "UNTESTED",
    "NO_DATA": "UNTESTED",
    # Exempt / Waived
    "NOT_APPLICABLE": "NOT_APPLICABLE",
    "N_A": "NOT_APPLICABLE",
    "NA": "NOT_APPLICABLE",
    "WAIVED": "NOT_APPLICABLE",
    "EXEMPT": "NOT_APPLICABLE",
    # Inconclusive
    "INCONCLUSIVE": "INCONCLUSIVE",
    "UNKNOWN": "INCONCLUSIVE",
    "AMBIGUOUS": "INCONCLUSIVE",
    "UNRESOLVED": "INCONCLUSIVE",
}

_TOP_LEVEL_STATUS_SYNONYMS: dict[str, VerificationTopLevelStatus] = {
    "SUPPORTED": "SUPPORTED",
    "PASS": "SUPPORTED",
    "PASSED": "SUPPORTED",
    "VERIFIED": "SUPPORTED",
    "MET": "SUPPORTED",
    "COMPLIANT": "SUPPORTED",
    "CLOSED": "SUPPORTED",
    "PARTIAL": "PARTIAL",
    "PARTIALLY_SUPPORTED": "PARTIAL",
    "IN_PROGRESS": "PARTIAL",
    "PARTIAL_EVIDENCE": "PARTIAL",
    "PENDING": "PARTIAL",
    "CONFLICT": "CONFLICT",
    "CONTRADICTION": "CONFLICT",
    "FAIL": "CONFLICT",
    "FAILED": "CONFLICT",
    "VIOLATION": "CONFLICT",
    "POTENTIAL_CONFLICT": "CONFLICT",
    "MISSING": "MISSING",
    "MISSING_EVIDENCE": "MISSING",
    "NOT_STARTED": "MISSING",
    "NO_EVIDENCE": "MISSING",
    "UNTESTED": "MISSING",
    "UNKNOWN": "UNKNOWN",
    "INCONCLUSIVE": "UNKNOWN",
    "SIMULATION_ONLY": "UNKNOWN",
    "AMBIGUOUS": "UNKNOWN",
    "UNVERIFIED": "UNKNOWN",
}


def normalize_condition_status(raw: Any) -> AtomicConditionStatus:
    """Normalize free-form LLM condition status into canonical AtomicConditionStatus."""
    if not isinstance(raw, str):
        return "UNTESTED"
    clean = raw.strip().upper().replace(" ", "_").replace("-", "_").replace(".", "_").replace("/", "_").strip("_")
    return _CONDITION_STATUS_SYNONYMS.get(clean, "UNTESTED")


def normalize_top_level_status(raw: Any) -> VerificationTopLevelStatus:
    """Normalize free-form LLM top-level status into canonical VerificationTopLevelStatus."""
    if not isinstance(raw, str):
        return "UNKNOWN"
    clean = raw.strip().upper().replace(" ", "_").replace("-", "_").replace(".", "_").replace("/", "_").strip("_")
    return _TOP_LEVEL_STATUS_SYNONYMS.get(clean, "UNKNOWN")


class RequirementCondition(BaseModel):
    """A distinct mandatory technical condition extracted from a requirement."""
    condition_id: Optional[str] = None
    condition: str
    parameter: Optional[str] = None
    required_value: Optional[str] = None
    unit: Optional[str] = None
    is_mandatory: bool = True


class ConditionVerificationResult(BaseModel):
    """Evaluation result for an individual atomic condition."""
    condition_id: str
    description: Optional[str] = None
    status: AtomicConditionStatus = "UNTESTED"
    # Pipeline-owned audit fields are intentionally omitted from the JSON
    # schema shown to the LLM. The model supplies semantic facts; Python owns
    # validation metadata.
    validation_state: SkipJsonSchema[ConditionValidationState] = "UNRESOLVED"
    validation_notes: SkipJsonSchema[list[str]] = Field(default_factory=list)
    observed_parameter: Optional[str] = None
    observed_value: Optional[str] = None
    observed_min_value: Optional[float] = None
    observed_max_value: Optional[float] = None
    observed_unit: Optional[str] = None
    evidence_value_role: EvidenceValueRole = "UNCLEAR"
    relationship: EvidenceRelationship = "UNCLEAR"
    evidence_ids: list[str] = Field(default_factory=list)
    quote: Optional[str] = None
    reason: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v: Any) -> str:
        return normalize_condition_status(v)

    @field_validator("observed_value", mode="before")
    @classmethod
    def _coerce_observed_value(cls, value: Any) -> Optional[str]:
        """Accept harmless scalar variations without rejecting a whole LLM batch."""
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return str(value).strip() or None

    @field_validator("observed_min_value", "observed_max_value", mode="before")
    @classmethod
    def _coerce_observed_bound(cls, value: Any) -> Optional[float]:
        """Parse numeric bounds even when the model includes the unit in the scalar."""
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?", value)
            if match:
                try:
                    return float(match.group(0).replace(",", "."))
                except ValueError:
                    return None
        # An unusable optional audit field must not invalidate sibling results.
        return None

    @field_validator("observed_unit", mode="before")
    @classmethod
    def _coerce_observed_unit(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

class EvidenceFinding(BaseModel):
    """An explicit finding observed from a specific evidence excerpt."""
    evidence_id: Optional[str] = None
    document_name: Optional[str] = None
    document_type: Optional[Literal["test_report", "datasheet", "specification", "compliance_matrix", "architecture_spec", "other"]] = "other"
    finding: str
    supports_condition: bool
    is_partial_or_pending: bool = False
    is_contradiction: bool = False
    quote: str


class VerificationAnalysisResult(BaseModel):
    """Structured output from multi-condition compliance evaluation."""
    status: VerificationTopLevelStatus = "UNKNOWN"
    confidence: int = Field(default=85, ge=0, le=100)
    requirement_conditions: list[RequirementCondition] = Field(default_factory=list)
    condition_results: list[ConditionVerificationResult] = Field(default_factory=list)
    evidence_findings: list[EvidenceFinding] = Field(default_factory=list)
    reason: str
    highlight: Optional[str] = None

    # Internal-only pipeline provenance.  A private attribute keeps diagnostic
    # bookkeeping out of the structured-output schema sent to the LLM while
    # still allowing the benchmark and API adapters to explain every
    # deterministic rewrite performed after model inference.
    _diagnostics: dict[str, Any] = PrivateAttr(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v: Any) -> str:
        return normalize_top_level_status(v)


class BatchVerificationItemResult(BaseModel):
    """Evaluation result for a single requirement within a batch."""
    req_code: str
    status: VerificationTopLevelStatus = "UNKNOWN"
    confidence: int = Field(default=85, ge=0, le=100)
    condition_results: list[ConditionVerificationResult] = Field(default_factory=list)
    reason: str
    highlight: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v: Any) -> str:
        return normalize_top_level_status(v)


class BatchVerificationResult(BaseModel):
    """Batch verification payload from Gemini LLM."""
    batch_results: list[BatchVerificationItemResult] = Field(default_factory=list)
