"""Pydantic Schema for Structured Multi-Condition Verification Reasoning."""

from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, field_validator


AtomicConditionStatus = Literal["PROVEN", "FAILED", "PENDING", "UNTESTED", "NOT_APPLICABLE", "INCONCLUSIVE"]
VerificationTopLevelStatus = Literal["SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT"]


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
    "UNKNOWN": "UNTESTED",
    # Exempt / Waived
    "NOT_APPLICABLE": "NOT_APPLICABLE",
    "N_A": "NOT_APPLICABLE",
    "NA": "NOT_APPLICABLE",
    "WAIVED": "NOT_APPLICABLE",
    "EXEMPT": "NOT_APPLICABLE",
    # Inconclusive
    "INCONCLUSIVE": "INCONCLUSIVE",
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
    evidence_ids: list[str] = Field(default_factory=list)
    quote: Optional[str] = None
    reason: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v: Any) -> str:
        return normalize_condition_status(v)


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
